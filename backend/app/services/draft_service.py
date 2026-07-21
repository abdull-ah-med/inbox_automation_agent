"""Draft orchestration — Sonnet call + persist + status updates."""

from __future__ import annotations

import uuid

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import DraftGenerationError
from app.llm import draft_generator as draft_llm
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email_triage_state import EmailTriageState
from app.repositories import draft_repo

logger = structlog.get_logger(__name__)


async def run_draft(
    state: EmailTriageState,
    *,
    session: AsyncSession,
    client: AsyncAnthropic,
    settings: Settings,
    thread_id: uuid.UUID,
    cross_thread_context: str | None = None,
    tone_references: list[str] | None = None,
) -> EmailTriageState:
    """Generate and persist a draft when ``draft_status`` is PENDING.

    Idempotent: if a draft already exists for the message, reuse it and skip the LLM.
    On ``DraftGenerationError``, set ``REQUIRES_HUMAN`` and return (caller audits).
    """
    message_id = state.original_email.message_id

    existing = await draft_repo.get_draft_by_message(session, message_id=message_id)
    if existing is not None:
        state.draft = DraftSchema.model_validate(
            existing.model_dump(include=set(DraftSchema.model_fields))
        )
        state.draft_status = "DRAFTED"
        logger.info(
            "draft_reused",
            conversation_id=state.original_email.conversation_id,
            mailbox=state.original_email.mailbox,
            message_id=message_id,
            urgency=existing.urgency,
        )
        return state

    if state.triage is None:
        state.draft_status = "REQUIRES_HUMAN"
        state.error_logs.append("draft_skipped:missing_triage")
        return state

    try:
        result = await draft_llm.generate_draft(
            state.original_email,
            state.thread_context,
            state.triage,
            client=client,
            settings=settings,
            cross_thread_context=cross_thread_context,
            tone_references=tone_references,
        )
    except DraftGenerationError as exc:
        logger.warning(
            "draft_generation_failed",
            conversation_id=state.original_email.conversation_id,
            mailbox=state.original_email.mailbox,
            message_id=message_id,
            error_type=type(exc).__name__,
        )
        state.draft_status = "REQUIRES_HUMAN"
        state.error_logs.append(f"draft_failed:{type(exc).__name__}")
        return state

    persisted = await draft_repo.create_draft(
        session,
        thread_id=thread_id,
        message_id=message_id,
        draft=result.draft,
        prompt_version=result.prompt_version,
    )
    state.draft = DraftSchema.model_validate(
        persisted.model_dump(include=set(DraftSchema.model_fields))
    )
    state.draft_status = "DRAFTED"
    logger.info(
        "draft_persisted",
        conversation_id=state.original_email.conversation_id,
        mailbox=state.original_email.mailbox,
        message_id=message_id,
        prompt_version=result.prompt_version,
        urgency=result.draft.urgency,
        model=result.model,
        latency_ms=result.latency_ms,
    )
    return state
