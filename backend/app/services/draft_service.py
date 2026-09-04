"""Draft orchestration — Sonnet call + persist + status updates."""

from __future__ import annotations

import uuid

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import DraftGenerationError
from app.llm import draft_generator as draft_llm
from app.llm.email_clean import effective_body_text
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema, EmailTriageState
from app.repositories import draft_repo
from app.services import (
    rejection_memory_service,
    skill_selection_service,
    tone_profile_service,
    urgency_feedback_service,
)

logger = structlog.get_logger(__name__)


async def run_draft(
    state: EmailTriageState,
    *,
    session: AsyncSession,
    client: AsyncAnthropic,
    settings: Settings,
    thread_id: uuid.UUID,
    cross_thread_context: CrossThreadContextSchema | str | None = None,
    tone_references: list[str] | None = None,
    tone_profile: str | None = None,
    skills: list[str] | None = None,
    negative_constraints: list[str] | None = None,
    openai_client: AsyncOpenAI | None = None,
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

    email = state.original_email
    body = effective_body_text(
        body_clean=email.body_clean,
        body_text=email.body_text,
        body_content_type=email.body_content_type,
    )
    email_text = f"{email.subject}\n\n{body}"
    category = state.triage.routing_category or "general"

    skill_contents = skills
    skill_ids: list[uuid.UUID] = []
    applied_skills: list = []
    if skill_contents is None:
        try:
            selected = await skill_selection_service.select_skills(
                session,
                client=client,
                settings=settings,
                openai_client=openai_client,
                email=email,
                triage=state.triage,
                conversation_id=email.conversation_id,
            )
            skill_contents = selected.blocks
            skill_ids = selected.skill_ids
            applied_skills = list(selected.applied)
        except Exception:
            logger.exception(
                "skill_load_failed",
                conversation_id=email.conversation_id,
                mailbox=email.mailbox,
                message_id=message_id,
            )
            skill_contents = []
            skill_ids = []
            applied_skills = []

    resolved_profile = tone_profile
    resolved_tone_refs = tone_references
    if resolved_profile is None and resolved_tone_refs is None:
        resolved_profile, resolved_tone_refs = await tone_profile_service.load_for_draft(
            session,
            openai_client=openai_client,
            settings=settings,
            mailbox=email.mailbox,
            routing_category=category,
            email_text=email_text,
        )

    resolved_constraints = negative_constraints
    if resolved_constraints is None:
        resolved_constraints = await rejection_memory_service.find_negative_constraints(
            session,
            openai_client=openai_client,
            settings=settings,
            email_text=email_text,
            mailbox=email.mailbox,
            routing_category=category,
            limit=3,
        )

    urgency_hints = await urgency_feedback_service.find_urgency_hints(
        session,
        openai_client=openai_client,
        settings=settings,
        email_text=email_text,
        mailbox=email.mailbox,
        routing_category=category,
        limit=3,
    )

    reference_loader = None
    if skill_ids:
        from app.services.skill_reference_service import make_reference_loader

        reference_loader, _ = make_reference_loader(active_skill_ids=set(skill_ids))

    from app.services import related_thread_service

    confirmed_associations = await related_thread_service.load_confirmed_contexts(
        session,
        thread_id,
    )

    directory: dict[str, str] | None = None
    if settings.salute_directory_enabled:
        from app.services import directory_lookup_service

        directory = await directory_lookup_service.build_directory(
            session,
            state.original_email.mailbox,
            state.thread_context,
            current=state.original_email,
        )

    from app.core.internal_mail import extract_email_address
    from app.services import thread_context_service

    working_memory = await thread_context_service.load_draft_working_memory(
        session,
        thread_id,
        settings=settings,
        mailbox=state.original_email.mailbox,
        recipient=extract_email_address(state.original_email.sender),
    )

    try:
        result = await draft_llm.generate_draft(
            state.original_email,
            state.thread_context,
            state.triage,
            client=client,
            settings=settings,
            cross_thread_context=cross_thread_context,
            tone_references=resolved_tone_refs,
            tone_profile=resolved_profile,
            skills=skill_contents,
            negative_constraints=resolved_constraints,
            urgency_hints=urgency_hints,
            reference_loader=reference_loader,
            confirmed_associations=confirmed_associations,
            directory=directory,
            user_notes=working_memory.get("user_notes", ""),
            facts=working_memory.get("facts") or None,
            prior_sends=working_memory.get("prior_sends") or None,
            org_identity=working_memory.get("org_identity", ""),
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

    confidence: float | None = None
    if isinstance(cross_thread_context, CrossThreadContextSchema):
        confidence = cross_thread_context.similarity_score

    persisted = await draft_repo.create_draft(
        session,
        thread_id=thread_id,
        message_id=message_id,
        draft=result.draft,
        prompt_version=result.prompt_version,
        context_match_confidence=confidence,
        routing_category=(state.triage.routing_category if state.triage is not None else None),
        tool_calls=result.tool_calls or None,
        applied_skills=applied_skills or None,
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
        context_match_confidence=confidence,
        skills_count=len(skill_contents),
        tool_call_count=len(result.tool_calls),
    )
    return state
