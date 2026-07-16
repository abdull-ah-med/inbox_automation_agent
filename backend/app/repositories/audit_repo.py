"""Audit event repository — append-only inserts into ``audit_events``."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.audit_event import AuditEvent
from app.models.schemas.audit import AuditEventSchema


async def create_audit_event(
    session: AsyncSession,
    *,
    event_type: str,
    conversation_id: str,
    mailbox: str,
    payload: dict[str, Any] | None = None,
    actor: str = "system",
) -> AuditEventSchema:
    """Insert one audit row and return the Pydantic schema (no commit)."""
    event = AuditEvent(
        event_type=event_type,
        conversation_id=conversation_id,
        mailbox=mailbox,
        payload=dict(payload or {}),
        actor=actor,
    )
    session.add(event)
    await session.flush()
    await session.refresh(event)
    return AuditEventSchema.model_validate(event)
