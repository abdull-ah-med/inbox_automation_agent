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
from app.core.exceptions import SearchError, ThreadNotFoundError, ThreadStateError
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
from app.services.related_match import (
    RelatedCandidate,
    alert_cluster_keys,
    base_subject,
    drip_key,
    normalize_sender,
    select_related,
    subjects_near,
)

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


def _deadlines_from_messages(rows) -> tuple[str, ...]:
    inbound = [row for row in rows if str(row.direction).lower() == "inbound"]
    if not inbound:
        return ()
    payload = inbound[-1].summary_json or {}
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("deadlines")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if item)


def _sender_from_messages(rows) -> str:
    inbound = [row for row in rows if str(row.direction).lower() == "inbound"]
    if inbound:
        return inbound[-1].sender
    if rows:
        return rows[-1].sender
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
    query_sender = await _latest_inbound_sender(session, source_thread.id)
    query = (
        f"from:{query_sender} {source_thread.subject}".strip()
        if query_sender
        else source_thread.subject
    )
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

    from datetime import UTC, datetime

    clock = source_thread.last_message_at or datetime.now(UTC)
    sql_rows = []
    if source_thread.alert_fingerprint:
        sql_rows = await thread_repo.list_alert_association_candidates(
            session,
            mailbox=source_thread.mailbox,
            fingerprint=source_thread.alert_fingerprint,
            signature=source_thread.alert_signature,
            sender_norm=source_thread.alert_sender_norm,
            now=clock,
        )

    load_ids = {source_thread.id}
    load_ids.update(hit.thread_id for hit in search.hits)
    load_ids.update(confirmed_ids)
    load_ids.update(row.id for row in sql_rows)
    load_ids.update(related_id for related_id, status in statuses.items() if status == "dismissed")
    grouped = await message_repo.list_by_thread_ids(session, list(load_ids))
    source_rows = grouped.get(source_thread.id, [])
    sender = _sender_from_messages(source_rows) or query_sender
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
        deadlines=_deadlines_from_messages(source_rows),
    )

    candidates: list[RelatedCandidate] = []
    seen: set[uuid.UUID] = set()

    def _append_candidate(
        *,
        thread_id: uuid.UUID,
        mailbox: str,
        conversation_id: str,
        subject: str,
        last_message_at,
        urgency: str | None,
        score: float,
        cosine: float | None,
    ) -> None:
        if thread_id in seen:
            return
        seen.add(thread_id)
        rows = grouped.get(thread_id, [])
        candidates.append(
            RelatedCandidate(
                thread_id=thread_id,
                mailbox=mailbox,
                conversation_id=conversation_id,
                subject=subject or "",
                sender=_sender_from_messages(rows),
                last_message_at=last_message_at,
                urgency=urgency,
                score=score,
                cosine=cosine,
                deadlines=_deadlines_from_messages(rows),
            )
        )

    for hit in search.hits:
        _append_candidate(
            thread_id=hit.thread_id,
            mailbox=hit.mailbox,
            conversation_id=hit.conversation_id,
            subject=hit.subject or "",
            last_message_at=hit.last_message_at,
            urgency=hit.urgency,
            score=hit.score,
            cosine=hit.similarity_score,
        )
    for row in sql_rows:
        if row.id == source_thread.id:
            continue
        score = 1.0 if row.alert_fingerprint == source_thread.alert_fingerprint else 0.9
        _append_candidate(
            thread_id=row.id,
            mailbox=row.mailbox,
            conversation_id=row.conversation_id,
            subject=row.subject,
            last_message_at=row.last_message_at,
            urgency=row.urgency,
            score=score,
            cosine=None,
        )
    extra_ids = set(confirmed_ids)
    extra_ids.update(
        related_id for related_id, status in statuses.items() if status == "dismissed"
    )
    extra_threads = await thread_repo.list_by_ids(session, list(extra_ids))
    if purpose == "associated":
        for related_id in confirmed_ids:
            related = extra_threads.get(related_id)
            if related is None:
                continue
            _append_candidate(
                thread_id=related.id,
                mailbox=related.mailbox,
                conversation_id=related.conversation_id,
                subject=related.subject,
                last_message_at=related.last_message_at,
                urgency=related.urgency,
                score=1.0,
                cosine=1.0,
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
        related = extra_threads.get(related_id)
        if related is None:
            continue
        sender_for_key = _sender_from_messages(grouped.get(related_id, []))
        if not sender_for_key:
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
                match_reasons=list(row.match_reasons),
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
    if thread_id == related_id:
        raise ThreadStateError("A thread cannot be associated with itself")
    related = await thread_repo.get_by_id(session, related_id)
    if related is None or not settings.mailbox_allowed(related.mailbox):
        raise ThreadNotFoundError(f"Thread not found: {related_id}")
    statuses = await association_review_repo.statuses_for_source(session, thread_id)
    existing = statuses.get(related_id)
    if status == "confirmed" and existing not in ("proposed", "confirmed"):
        raise ThreadStateError("Association was not proposed")
    await association_review_repo.set_status(
        session,
        source_thread_id=thread_id,
        related_thread_id=related_id,
        status=status,
        actor=actor,
    )
    if status == "dismissed":
        await association_review_repo.set_status(
            session,
            source_thread_id=related_id,
            related_thread_id=thread_id,
            status="dismissed",
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
    source = await thread_repo.get_by_id(session, source_thread_id)
    keep_ids = [related_id for related_id, status in statuses.items() if status != "dismissed"]
    related_map = await thread_repo.list_by_ids(session, keep_ids)
    load_ids = list(related_map.keys())
    if source is not None:
        load_ids.append(source.id)
    grouped = await message_repo.list_by_thread_ids(session, load_ids)
    source_sender = _sender_from_messages(grouped.get(source.id, [])) if source else ""
    items: list[RelatedThreadItem] = []
    for related_id, status in statuses.items():
        if status == "dismissed":
            continue
        related = related_map.get(related_id)
        if related is None:
            continue
        related_sender = _sender_from_messages(grouped.get(related.id, []))
        reasons: list[str] = []
        if source is not None:
            same_sender = normalize_sender(source_sender) == normalize_sender(related_sender)
            src_base = base_subject(source.subject)
            rel_base = base_subject(related.subject)
            if same_sender:
                reasons.append("same_sender")
            if src_base and src_base == rel_base:
                reasons.append("same_subject")
            elif same_sender and subjects_near(source.subject, related.subject):
                reasons.append("near_subject")
        items.append(
            RelatedThreadItem(
                thread_id=related.id,
                mailbox=related.mailbox,
                subject=related.subject,
                sender=related_sender,
                last_message_at=related.last_message_at,
                urgency=related.urgency,
                score=0.0,
                status=status if status in ("proposed", "confirmed") else "proposed",
                match_reasons=reasons,
            )
        )
    return items


async def propose_alert_associations(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    now=None,
) -> list[uuid.UUID]:
    """Propose associations for automated alerts without OpenAI/search.

    Uses persisted alert_fingerprint exact/near-subject SQL (90d window).
    """
    from datetime import UTC, datetime

    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)

    source = await thread_repo.get_by_id(session, thread_id)
    if source is None:
        return []
    fingerprint = source.alert_fingerprint
    signature = source.alert_signature
    sender_norm = source.alert_sender_norm
    if not fingerprint or not signature or not sender_norm:
        sender = await _latest_inbound_sender(session, source.id)
        keys = alert_cluster_keys(
            mailbox=source.mailbox,
            sender=sender,
            subject=source.subject,
        )
        if keys is None:
            return []
        fingerprint, signature, sender_norm = keys
        await thread_repo.set_alert_fingerprint(
            session,
            source.id,
            fingerprint,
            signature=signature,
            sender_norm=sender_norm,
        )

    candidates = await thread_repo.list_alert_association_candidates(
        session,
        mailbox=source.mailbox,
        fingerprint=fingerprint,
        signature=signature,
        sender_norm=sender_norm,
        now=clock,
        window_days=90,
    )
    proposed: list[uuid.UUID] = []
    source_sender = await _latest_inbound_sender(session, source.id)
    for related in candidates:
        if related.id == source.id:
            continue
        if related.conversation_id == source.conversation_id:
            continue
        score = 1.0 if related.alert_fingerprint == fingerprint else 0.9
        await association_review_repo.upsert_proposed(
            session,
            source_thread_id=source.id,
            related_thread_id=related.id,
            score=score,
        )
        await association_review_repo.upsert_proposed(
            session,
            source_thread_id=related.id,
            related_thread_id=source.id,
            score=score,
        )
        proposed.append(related.id)
        logger.info(
            "alert_association_proposed",
            source_thread_id=str(source.id),
            related_thread_id=str(related.id),
            mailbox=source.mailbox,
            fingerprint_len=len(fingerprint),
            score=score,
        )
    _ = source_sender  # reserved for future deadline-enrichment reasons
    return proposed
