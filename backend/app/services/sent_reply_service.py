"""Detect outbound Sent Items replies and mark threads RESOLVED."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_scope import TenantScope
from app.models.schemas.email import ThreadStateEnum
from app.repositories import draft_repo, message_repo, sent_reply_repo, thread_repo
from app.repositories.message_repo import MessageSchema
from app.repositories.sent_reply_repo import MatchedBy, SentReplySchema
from app.services import audit_service
from app.services.thread_view_service import reply_text_for_message

logger = structlog.get_logger(__name__)

_SENT_BODY_CAP = 20_000
_TIME_WINDOW = timedelta(hours=48)

# Graph eventMessage.meetingMessageType values that are calendar mail, not replies.
_MEETING_MESSAGE_TYPES = frozenset(
    {
        "meetingRequest",
        "meetingCancelled",
        "meetingAccepted",
        "meetingTenativelyAccepted",
        "meetingDeclined",
    }
)


def is_meeting_message(message: MessageSchema) -> bool:
    """True when Graph identified this row as an event/meeting message."""
    return (message.meeting_message_type or "") in _MEETING_MESSAGE_TYPES


async def thread_tip_already_replied(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> bool:
    """True when the tip message is still the Outlook send we resolved on.

    A newer inbound after resolve must return False so drafting can reopen.
    """
    messages = await message_repo.list_by_thread(session, thread_id)
    if not messages:
        return False
    tip = max(messages, key=lambda m: m.received_at)
    if tip.direction != "outbound":
        return False
    sent = await sent_reply_repo.get_by_thread(session, thread_id)
    if sent is None:
        return False
    return tip.id == sent.message_id


def _cap_body(body: str) -> str:
    if len(body) <= _SENT_BODY_CAP:
        return body
    return body[:_SENT_BODY_CAP]


def _normalize_sent_at(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _pick_draft_match(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    sent_at: datetime,
) -> tuple[uuid.UUID | None, MatchedBy]:
    """Choose draft linkage: approved_draft first, else most recent within 48h."""
    drafts = await draft_repo.list_by_thread(session, thread_id)
    if not drafts:
        return None, "time_window"

    for draft in drafts:
        if draft.approved_at is None:
            continue
        approved_at = _normalize_sent_at(draft.approved_at)
        if approved_at >= sent_at:
            continue
        existing = await sent_reply_repo.get_by_draft(session, draft.id)
        if existing is not None:
            continue
        return draft.id, "approved_draft"

    window_start = sent_at - _TIME_WINDOW
    for draft in drafts:
        created = _normalize_sent_at(draft.created_at)
        if window_start <= created <= sent_at:
            return draft.id, "time_window"

    return None, "time_window"


async def resolve_thread_from_outbound(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    message: MessageSchema,
    conversation_id: str,
    mailbox: str,
) -> SentReplySchema | None:
    """Link an outbound message to a draft (when possible) and set RESOLVED.

    Idempotent on ``message.id`` via unique constraint. Never logs body text.
    Calendar accepts/declines/cancels still resolve (Elise acted) but do not
    attach a drafted letter — they are not email replies to learn from.
    """
    sent_at = _normalize_sent_at(message.received_at)
    if is_meeting_message(message):
        draft_id: uuid.UUID | None = None
        matched_by: MatchedBy = "time_window"
    else:
        draft_id, matched_by = await _pick_draft_match(
            session,
            thread_id=thread_id,
            sent_at=sent_at,
        )

    sent_reply, created = await sent_reply_repo.insert_sent_reply(
        session,
        thread_id=thread_id,
        message_id=message.id,
        draft_id=draft_id,
        sent_body_snapshot=_cap_body(
            reply_text_for_message(
                body_text=message.body_text,
                unique_body_text=message.unique_body_text,
                body_preview=message.body_preview,
            )
        ),
        sent_at=sent_at,
        matched_by=matched_by,
    )

    if not created:
        logger.info(
            "sent_reply_duplicate",
            thread_id=str(thread_id),
            message_id=str(message.id),
            mailbox=mailbox,
        )
        return sent_reply

    await thread_repo.set_thread_outcome(
        session,
        thread_id,
        state=ThreadStateEnum.RESOLVED.value,
    )

    try:
        thread = await thread_repo.get_by_id(session, thread_id, TenantScope.single(mailbox))
        urgency_assessed = thread.urgency if thread is not None else None
        matched_label = matched_by.replace("_", " ")
        human_body = f"Matched your Outlook send ({matched_label}). Removed from Needs Attention."
        if urgency_assessed:
            human_body = (
                f"{human_body} Assessed urgency was {urgency_assessed}; "
                "it no longer drives priority."
            )
        await audit_service.log_event(
            session,
            event_type="thread.resolved.sent_reply_detected",
            conversation_id=conversation_id,
            mailbox=mailbox,
            payload={
                "message_id": str(message.id),
                "draft_id": str(draft_id) if draft_id else None,
                "matched_by": matched_by,
                "urgency_assessed": urgency_assessed,
                "human": {
                    "title": "Resolved from sent reply",
                    "body": human_body,
                    "actor_kind": "agent",
                },
            },
            actor="system",
        )
    except Exception:
        logger.warning(
            "sent_reply_audit_failed",
            thread_id=str(thread_id),
            message_id=str(message.id),
        )

    logger.info(
        "sent_reply_resolved",
        thread_id=str(thread_id),
        message_id=str(message.id),
        draft_id=str(draft_id) if draft_id else None,
        matched_by=matched_by,
        mailbox=mailbox,
    )
    return sent_reply
