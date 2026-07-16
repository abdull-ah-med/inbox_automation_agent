"""Append-only audit event logging (Postgres ``audit_events`` — not DraftAssistant/RAM)."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuditError
from app.models.schemas.audit import AuditEventSchema
from app.repositories import audit_repo

logger = structlog.get_logger(__name__)


async def log_event(
    session: AsyncSession,
    *,
    event_type: str,
    conversation_id: str,
    mailbox: str,
    payload: dict[str, Any] | None = None,
    actor: str = "system",
) -> AuditEventSchema:
    """Persist an audit event. Raises ``AuditError`` on failure (callers may catch)."""
    try:
        return await audit_repo.create_audit_event(
            session,
            event_type=event_type,
            conversation_id=conversation_id,
            mailbox=mailbox,
            payload=payload,
            actor=actor,
        )
    except Exception as exc:
        logger.warning(
            "audit_write_failed",
            event_type=event_type,
            conversation_id=conversation_id,
            mailbox=mailbox,
            error_type=type(exc).__name__,
        )
        raise AuditError(f"Failed to write audit event: {type(exc).__name__}") from exc
