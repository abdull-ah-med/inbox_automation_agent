"""Detect outbound Sent Items replies and mark threads RESOLVED."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas.email import ThreadStateEnum
from app.repositories import draft_repo, sent_reply_repo, thread_repo
from app.repositories.message_repo import MessageSchema
from app.repositories.sent_reply_repo import MatchedBy, SentReplySchema
from app.services import audit_service

logger = structlog.get_logger(__name__)

_SENT_BODY_CAP = 20_000
_TIME_WINDOW = timedelta(hours=48)


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
    """
    sent_at = _normalize_sent_at(message.received_at)
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
        sent_body_snapshot=_cap_body(message.body_text),
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
        await audit_service.log_event(
            session,
            event_type="thread.resolved.sent_reply_detected",
            conversation_id=conversation_id,
            mailbox=mailbox,
            payload={
                "message_id": str(message.id),
                "draft_id": str(draft_id) if draft_id else None,
                "matched_by": matched_by,
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
