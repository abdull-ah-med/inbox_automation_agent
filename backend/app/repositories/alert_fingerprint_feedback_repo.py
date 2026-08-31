from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.alert_fingerprint_feedback import AlertFingerprintFeedback

SUPPRESS_ESCALATION = "suppress_escalation"


async def is_suppressed(session: AsyncSession, *, mailbox: str, fingerprint: str) -> bool:
    stmt = (
        select(AlertFingerprintFeedback.id)
        .where(
            AlertFingerprintFeedback.mailbox == mailbox,
            AlertFingerprintFeedback.fingerprint == fingerprint,
            AlertFingerprintFeedback.action == SUPPRESS_ESCALATION,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def insert_suppress(
    session: AsyncSession,
    *,
    mailbox: str,
    fingerprint: str,
    source_thread_id: uuid.UUID,
    actor: str,
    previous_urgency: str | None,
    assessed_urgency: str | None,
    note: str | None = None,
) -> None:
    session.add(
        AlertFingerprintFeedback(
            mailbox=mailbox,
            fingerprint=fingerprint,
            action=SUPPRESS_ESCALATION,
            source_thread_id=source_thread_id,
            actor=actor,
            previous_urgency=previous_urgency,
            assessed_urgency=assessed_urgency,
            note=note,
        )
    )
    await session.flush()
