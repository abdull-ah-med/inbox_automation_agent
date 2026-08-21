"""On-demand sibling/associated finder and local apply-treatment.

Search uses existing hybrid retrieval across ``settings.mailbox_list``.
Nothing writes to Outlook. Search failures return an empty proposal.
"""

from __future__ import annotations

import uuid

import structlog
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import SearchError, ThreadNotFoundError
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadStateEnum
from app.models.schemas.email_triage_state import CrossThreadContextSchema
from app.models.schemas.related import (
    RelatedReviewResponse,
    RelatedThreadItem,
    RelatedThreadList,
)
from app.models.schemas.search import SEARCH_MODE_HYBRID
from app.models.schemas.urgency_feedback import UrgencyLevel
from app.repositories import (
    association_review_repo,
    draft_repo,
    message_repo,
    thread_repo,
)
from app.services import audit_service, draft_feedback_service, search_service
from app.services.related_match import RelatedCandidate, drip_key, select_related

logger = structlog.get_logger(__name__)

_RELATED_SEARCH_LIMIT = 25


async def _require_thread(
    session: AsyncSession,
    thread_id: uuid.UUID,
    settings: Settings,
):
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise ThreadNotFoundError(f"Thread not found: {thread_id}")
    return thread


async def _latest_inbound_sender(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> str:
    messages = await message_repo.list_by_thread(session, thread_id)
    inbound = [row for row in messages if row.direction == "inbound"]
    if inbound:
        return inbound[-1].sender
    if messages:
        return messages[-1].sender
    return ""


async def list_related(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
    *,
    purpose: str,
    openai_client: AsyncOpenAI | None,
    actor: str | None = None,
) -> RelatedThreadList:
    _ = actor
    if purpose not in ("siblings", "associated"):
        purpose = "siblings"
    source_thread = await _require_thread(session, thread_id, settings)
    sender = await _latest_inbound_sender(session, source_thread.id)
    source = RelatedCandidate(
        thread_id=source_thread.id,
        mailbox=source_thread.mailbox,
        conversation_id=source_thread.conversation_id,
        subject=source_thread.subject,
        sender=sender,
        last_message_at=source_thread.last_message_at,
        urgency=source_thread.urgency,
        score=0.0,
        cosine=None,
    )
    query = f"from:{sender} {source_thread.subject}".strip() if sender else source_thread.subject
    try:
        search = await search_service.search_threads(
            session,
            settings,
            openai_client=openai_client,
            query=query,
            mailbox=None,
            limit=_RELATED_SEARCH_LIMIT,
            mode=SEARCH_MODE_HYBRID,
            allow_empty=True,
        )
    except SearchError:
        logger.warning("related_search_failed", thread_id=str(thread_id), purpose=purpose)
        return RelatedThreadList(items=[])

    statuses = await association_review_repo.statuses_for_source(session, source_thread.id)
    dismissed_ids = {
        related_id for related_id, status in statuses.items() if status == "dismissed"
    }
    confirmed_ids = {
        related_id for related_id, status in statuses.items() if status == "confirmed"
    }

    candidates: list[RelatedCandidate] = []
    senders_by_thread: dict[uuid.UUID, str] = {}
    for hit in search.hits:
        if hit.thread_id not in senders_by_thread:
            senders_by_thread[hit.thread_id] = await _latest_inbound_sender(
                session, hit.thread_id
            )
        candidates.append(
            RelatedCandidate(
                thread_id=hit.thread_id,
                mailbox=hit.mailbox,
                conversation_id=hit.conversation_id,
                subject=hit.subject or "",
                sender=senders_by_thread[hit.thread_id],
                last_message_at=hit.last_message_at,
                urgency=hit.urgency,
                score=hit.score,
                cosine=hit.similarity_score,
            )
        )

    if purpose == "associated":
        candidate_ids = {c.thread_id for c in candidates}
        missing_confirmed = [rid for rid in confirmed_ids if rid not in candidate_ids]
        for related_id in missing_confirmed:
            related = await thread_repo.get_by_id(session, related_id)
            if related is None:
                continue
            candidates.append(
                RelatedCandidate(
                    thread_id=related.id,
                    mailbox=related.mailbox,
                    conversation_id=related.conversation_id,
                    subject=related.subject,
                    sender=await _latest_inbound_sender(session, related.id),
                    last_message_at=related.last_message_at,
                    urgency=related.urgency,
                    score=1.0,
                    cosine=1.0,
                )
            )

    queue_ids: set[uuid.UUID] = set()
    if purpose == "siblings":
        queue_ids = await thread_repo.ids_needing_attention(
            session,
            [row.thread_id for row in candidates],
        )

    suppressed: set[tuple[str, str]] = set()
    for related_id, status in statuses.items():
        if status != "dismissed":
            continue
        related = await thread_repo.get_by_id(session, related_id)
        if related is None:
            continue
        sender_for_key = await _latest_inbound_sender(session, related.id)
        suppressed.add(drip_key(sender_for_key, related.subject))

    selected = select_related(
        source,
        candidates,
        purpose=purpose,
        dismissed_ids=dismissed_ids,
        min_similarity=settings.embedding_min_similarity,
        queue_ids=queue_ids,
        confirmed_ids=confirmed_ids,
        suppressed_drip_keys=suppressed,
    )

    items: list[RelatedThreadItem] = []
    for row in selected:
        status = statuses.get(row.thread_id, "proposed")
        if status == "proposed":
            await association_review_repo.upsert_proposed(
                session,
                source_thread_id=source_thread.id,
                related_thread_id=row.thread_id,
                score=row.score,
            )
        items.append(
            RelatedThreadItem(
                thread_id=row.thread_id,
                mailbox=row.mailbox,
                subject=row.subject,
                sender=row.sender,
                last_message_at=row.last_message_at,
                urgency=row.urgency,
                score=row.score,
                status=status if status in ("proposed", "confirmed", "dismissed") else "proposed",
            )
        )
    return RelatedThreadList(items=items)


async def apply_treatment(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
    *,
    treatment: str,
    thread_ids: list[uuid.UUID],
    reason: str,
    actor: str,
    openai_client: AsyncOpenAI | None,
    urgency: UrgencyLevel | None = None,
) -> list[uuid.UUID]:
    related = await list_related(
        session,
        settings,
        thread_id,
        purpose="siblings",
        openai_client=openai_client,
        actor=actor,
    )
    allowed = {item.thread_id for item in related.items if item.status == "proposed"}
    applied: list[uuid.UUID] = []
    for related_id in thread_ids:
        if related_id not in allowed:
            continue
        if treatment == "no_reply":
            await _apply_no_reply(session, related_id, reason=reason, actor=actor)
        elif treatment == "urgency":
            if urgency is None:
                continue
            await _apply_urgency(
                session,
                related_id,
                urgency=urgency,
                reason=reason,
                actor=actor,
            )
        else:
            continue
        await association_review_repo.set_status(
            session,
            source_thread_id=thread_id,
            related_thread_id=related_id,
            status="confirmed",
            actor=actor,
        )
        applied.append(related_id)
    return applied


async def review_related(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
    related_id: uuid.UUID,
    *,
    status: str,
    actor: str,
) -> RelatedReviewResponse:
    await _require_thread(session, thread_id, settings)
    related = await thread_repo.get_by_id(session, related_id)
    if related is None or not settings.mailbox_allowed(related.mailbox):
        raise ThreadNotFoundError(f"Thread not found: {related_id}")
    await association_review_repo.set_status(
        session,
        source_thread_id=thread_id,
        related_thread_id=related_id,
        status=status,
        actor=actor,
    )
    return RelatedReviewResponse(status="confirmed" if status == "confirmed" else "dismissed")


async def _apply_no_reply(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    reason: str,
    actor: str,
) -> None:
    draft = await draft_repo.get_latest_by_thread(session, thread_id)
    if draft is not None:
        await draft_feedback_service.mark_wrong(
            session,
            draft.id,
            feedback_note=reason,
            reason_code="wrong_action",
            actor=actor,
        )
        return
    await thread_repo.set_thread_outcome(
        session,
        thread_id,
        state=ThreadStateEnum.NO_ACTION.value,
    )


async def _apply_urgency(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    urgency: UrgencyLevel,
    reason: str,
    actor: str,
) -> None:
    _ = actor
    draft = await draft_repo.get_latest_by_thread(session, thread_id)
    if draft is not None:
        await draft_repo.set_urgency(
            session,
            draft.id,
            urgency=urgency,
            urgency_reason=reason,
        )
    await thread_repo.set_urgency(
        session,
        thread_id,
        urgency=urgency,
        urgency_reason=reason,
    )
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None:
        return
    try:
        await audit_service.log_event(
            session,
            event_type="thread.urgency.edited",
            conversation_id=thread.conversation_id,
            mailbox=thread.mailbox,
            payload={
                "thread_id": str(thread_id),
                "new": urgency,
                "source": "apply_treatment",
            },
            actor=actor,
        )
    except Exception:
        logger.warning("related_urgency_audit_failed", thread_id=str(thread_id))


def _row_to_email(
    message,
    *,
    mailbox: str,
    conversation_id: str,
    subject: str,
) -> EmailMessageSchema:
    direction = (
        EmailDirectionEnum.OUTBOUND
        if str(message.direction).lower() == "outbound"
        else EmailDirectionEnum.INBOUND
    )
    return EmailMessageSchema(
        message_id=message.graph_message_id,
        conversation_id=conversation_id,
        mailbox=mailbox,
        sender=message.sender,
        subject=subject,
        body_text=message.body_text,
        body_preview=message.body_preview,
        body_content_type=message.body_content_type or "text",
        body_clean=message.body_clean,
        received_at=message.received_at,
        direction=direction,
        to_recipients=list(message.to_recipients or []),
        cc_recipients=list(message.cc_recipients or []),
        bcc_recipients=list(message.bcc_recipients or []),
        has_attachments=bool(message.has_attachments),
        summary_one_line=message.summary_one_line,
        summary_json=message.summary_json,
    )


async def load_confirmed_contexts(
    session: AsyncSession,
    source_thread_id: uuid.UUID,
) -> list[CrossThreadContextSchema]:
    """Local-DB messages for confirmed associations. Never packs unconfirmed hits."""
    pairs = await association_review_repo.confirmed_pairs(session, source_thread_id)
    contexts: list[CrossThreadContextSchema] = []
    for related_id, score in pairs:
        related = await thread_repo.get_by_id(session, related_id)
        if related is None:
            continue
        rows = await message_repo.list_by_thread(session, related.id)
        if not rows:
            continue
        contexts.append(
            CrossThreadContextSchema(
                matched_conversation_id=related.conversation_id,
                similarity_score=min(1.0, max(0.0, score or 1.0)),
                thread_messages=[
                    _row_to_email(
                        row,
                        mailbox=related.mailbox,
                        conversation_id=related.conversation_id,
                        subject=related.subject,
                    )
                    for row in rows
                ],
            )
        )
    return contexts


async def list_stored_associations(
    session: AsyncSession,
    source_thread_id: uuid.UUID,
) -> list[RelatedThreadItem]:
    statuses = await association_review_repo.statuses_for_source(session, source_thread_id)
    items: list[RelatedThreadItem] = []
    for related_id, status in statuses.items():
        if status == "dismissed":
            continue
        related = await thread_repo.get_by_id(session, related_id)
        if related is None:
            continue
        items.append(
            RelatedThreadItem(
                thread_id=related.id,
                mailbox=related.mailbox,
                subject=related.subject,
                sender=await _latest_inbound_sender(session, related.id),
                last_message_at=related.last_message_at,
                urgency=related.urgency,
                score=0.0,
                status=status if status in ("proposed", "confirmed") else "proposed",
            )
        )
    return items
