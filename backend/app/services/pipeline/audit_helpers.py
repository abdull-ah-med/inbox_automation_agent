"""Pipeline audit helpers (keeps service.py under LOC budget)."""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuditError
from app.models.schemas.email_triage_state import CrossThreadContextSchema, EmailTriageState
from app.services import audit_service

logger = structlog.get_logger(__name__)


async def safe_audit(
    session: AsyncSession,
    *,
    state: EmailTriageState,
    event_type: str,
    payload: dict[str, object],
) -> None:
    email = state.original_email
    try:
        await audit_service.log_event(
            session,
            event_type=event_type,
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            payload=payload,
            actor="system",
        )
    except AuditError:
        logger.exception(
            "pipeline_audit_failed",
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            event_type=event_type,
        )


def context_audit_payload(cross: CrossThreadContextSchema | None) -> dict[str, object]:
    if cross is None:
        return {}
    return {
        "similarity_score": cross.similarity_score,
        "matched_conversation_id": cross.matched_conversation_id,
    }
