"""Same-thread recurring automated alert detection and urgency flooring."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.automated_mail import is_automated_mail
from app.core.thread_policy import max_urgency, recurrence_urgency_floor
from app.models.schemas.email import ThreadStateEnum
from app.repositories import message_repo, thread_repo
from app.services import audit_service

logger = structlog.get_logger(__name__)

_FINISHED = frozenset(
    {
        ThreadStateEnum.RESOLVED.value,
        ThreadStateEnum.NO_ACTION.value,
        ThreadStateEnum.SPAM.value,
    }
)
_WINDOW = timedelta(hours=48)


async def count_automated_inbound_48h(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> int:
    """Count inbound automated messages in the last 48 hours for a thread."""
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    cutoff = clock - _WINDOW
    rows = await message_repo.list_by_thread(session, thread_id)
    count = 0
    for row in rows:
        if str(row.direction).lower() != "inbound":
            continue
        received = row.received_at
        if received.tzinfo is None:
            received = received.replace(tzinfo=UTC)
        if received < cutoff:
            continue
        # MessageSchema may not have subject; use thread subject via caller if needed.
        sender = row.sender
        if is_automated_mail(sender=sender, subject=None):
            count += 1
            continue
        # Fallback: also treat notification-style subjects on body_preview first line.
        preview = (row.body_preview or "")[:120]
        if is_automated_mail(sender=sender, subject=preview):
            count += 1
    return count


async def apply_recurrence_escalation(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    conversation_id: str,
    mailbox: str,
    assessed_urgency: str | None,
) -> str | None:
    """Raise urgency to the recurrence floor when open; return the applied urgency."""
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None:
        return assessed_urgency
    finished = thread.state in _FINISHED
    count = await count_automated_inbound_48h(session, thread_id)
    floor = recurrence_urgency_floor(
        automated_inbound_count_48h=count,
        is_finished=finished,
    )
    applied = max_urgency(assessed_urgency, floor)
    if floor is None or applied is None or applied == assessed_urgency:
        return assessed_urgency

    reason = f"{count} automated alerts in 48h; urgency floor {floor}"
    await thread_repo.set_urgency(
        session,
        thread_id,
        urgency=applied,
        urgency_reason=reason,
    )
    try:
        await audit_service.log_event(
            session,
            event_type="thread.urgency.recurrence_escalated",
            conversation_id=conversation_id,
            mailbox=mailbox,
            payload={
                "floor": floor,
                "count_48h": count,
                "previous": assessed_urgency,
                "new": applied,
                "human": {
                    "title": "Urgency raised for recurring alert",
                    "body": f"{count}th automated alert in 48h. Urgency raised to {applied}.",
                    "actor_kind": "agent",
                },
            },
            actor="system",
        )
    except Exception:
        logger.warning("recurrence_audit_failed", thread_id=str(thread_id))
    return applied


def recurrence_hint(count_48h: int) -> str | None:
    floor = recurrence_urgency_floor(automated_inbound_count_48h=count_48h)
    if floor is None:
        return None
    return (
        f"Same-thread recurring automated alert: {count_48h} in 48h. "
        f"Urgency should be at least {floor}."
    )
