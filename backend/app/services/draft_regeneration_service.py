"""Draft regeneration — re-run Sonnet with a reviewer instruction override."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import DraftGenerationError, ThreadNotFoundError, ThreadStateError
from app.core.tenant_scope import TenantScope
from app.llm import draft_generator as draft_llm
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.dashboard import TriageFlags
from app.models.schemas.draft import DraftResponseSchema
from app.models.schemas.email import (
    EmailDirectionEnum,
    EmailMessageSchema,
    ThreadContextSchema,
)
from app.repositories import (
    audit_repo,
    draft_repo,
    message_repo,
    thread_repo,
)
from app.services import (
    audit_service,
    rejection_memory_service,
    sent_reply_service,
    skill_selection_service,
    tone_profile_service,
    urgency_feedback_service,
)

logger = structlog.get_logger(__name__)

DraftAuditEvent = Literal["draft.regenerated", "draft.generated"]


def _message_to_email(
    message: message_repo.MessageSchema,
    *,
    mailbox: str,
    conversation_id: str,
    subject: str,
) -> EmailMessageSchema:
    direction = (
        EmailDirectionEnum.OUTBOUND
        if message.direction.lower() == "outbound"
        else EmailDirectionEnum.INBOUND
    )
    return EmailMessageSchema(
        message_id=message.graph_message_id,
        conversation_id=conversation_id,
        mailbox=mailbox,
        sender=message.sender,
        sender_display_name=message.sender_name,
        is_automated=bool(message.is_automated),
        subject=subject,
        body_text=message.body_text,
        body_preview=message.body_preview,
        received_at=message.received_at,
        direction=direction,
        to_recipients=list(message.to_recipients),
        cc_recipients=list(message.cc_recipients),
        bcc_recipients=list(message.bcc_recipients),
        has_attachments=bool(message.has_attachments),
        meeting_message_type=message.meeting_message_type,
    )


def _build_triage_from_flags(
    triage_flags: TriageFlags | None,
    *,
    draft_routing: str | None,
    force_letter: bool,
) -> TriageResultSchema:
    if triage_flags is None:
        triage = TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary=None,
            needs_context=False,
            routing_category=draft_routing or "general",
            draft_needed=True,
            is_automated=False,
        )
    else:
        routing = triage_flags.routing_category or draft_routing or "general"
        triage = TriageResultSchema(
            is_spam=bool(triage_flags.is_spam) if triage_flags.is_spam is not None else False,
            spam_reason=triage_flags.spam_reason,
            has_action_items=(
                bool(triage_flags.has_action_items)
                if triage_flags.has_action_items is not None
                else True
            ),
            action_items_summary=triage_flags.action_items_summary,
            needs_context=(
                bool(triage_flags.needs_context)
                if triage_flags.needs_context is not None
                else False
            ),
            context_reason=triage_flags.context_reason,
            routing_category=routing,
            draft_needed=(
                bool(triage_flags.draft_needed) if triage_flags.draft_needed is not None else False
            ),
            is_automated=(
                bool(triage_flags.is_automated) if triage_flags.is_automated is not None else False
            ),
        )
    if force_letter:
        if triage.is_spam:
            raise DraftGenerationError("Cannot generate draft for spam thread")
        triage.draft_needed = True
        triage.has_action_items = True
    return triage


async def regenerate_draft(
    session: AsyncSession,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    thread_id: uuid.UUID,
    instruction: str | None = None,
    actor: str = "user",
    openai_client: AsyncOpenAI | None = None,
    force_letter: bool = False,
    audit_event: DraftAuditEvent = "draft.regenerated",
    graph_client: object | None = None,
) -> DraftResponseSchema:
    """Create a NEW draft for a thread using optional reviewer instruction.

    Does not update or delete prior drafts. Does not send email.

    When ``force_letter`` is true, Sonnet always uses the letter path even if
    triage stored ``draft_needed=false`` (briefing threads).
    """
    thread = await thread_repo.get_by_id(session, thread_id, TenantScope.from_settings(settings))
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise ThreadNotFoundError(f"Thread not found: {thread_id}")

    messages = await message_repo.list_by_thread(session, thread_id)
    if not messages:
        raise DraftGenerationError("Cannot regenerate draft: thread has no messages")

    latest = max(messages, key=lambda row: row.received_at)
    if await sent_reply_service.graph_outbound_tip_in_sync(
        session,
        graph_client,
        mailbox=thread.mailbox,
        conversation_id=thread.conversation_id,
        thread_id=thread_id,
        trigger_graph_message_id=latest.graph_message_id,
    ):
        raise ThreadStateError(
            "Cannot generate a letter: the newest message is an outbound send already in Graph"
        )
    email = _message_to_email(
        latest,
        mailbox=thread.mailbox,
        conversation_id=thread.conversation_id,
        subject=thread.subject,
    )
    thread_context = ThreadContextSchema(
        conversation_id=thread.conversation_id,
        mailbox=thread.mailbox,
        subject=thread.subject,
        messages=[
            _message_to_email(
                msg,
                mailbox=thread.mailbox,
                conversation_id=thread.conversation_id,
                subject=thread.subject,
            )
            for msg in messages
        ],
    )

    triage_flags = await audit_repo.get_latest_triage_flags(
        session,
        thread.conversation_id,
        mailbox=thread.mailbox,
    )
    latest_draft = await draft_repo.get_latest_by_thread(session, thread_id)
    draft_routing = (
        latest_draft.routing_category
        if latest_draft is not None and latest_draft.routing_category
        else None
    )
    triage = _build_triage_from_flags(
        triage_flags,
        draft_routing=draft_routing,
        force_letter=force_letter,
    )

    active_skills_contents: list[str] = []
    skill_ids: list[uuid.UUID] = []
    applied_skills: list = []
    try:
        selected = await skill_selection_service.select_skills(
            session,
            client=client,
            settings=settings,
            openai_client=openai_client,
            email=email,
            triage=triage,
            conversation_id=thread.conversation_id,
        )
        active_skills_contents = selected.blocks
        skill_ids = selected.skill_ids
        applied_skills = list(selected.applied)
    except Exception:
        logger.exception(
            "skill_load_failed_on_regenerate",
            thread_id=str(thread_id),
        )
        active_skills_contents = []
        skill_ids = []
        applied_skills = []

    mailbox = thread.mailbox
    conversation_id = thread.conversation_id
    subject = thread.subject
    latest_body = latest.body_text
    latest_graph_id = latest.graph_message_id
    email_text = f"{subject}\n\n{latest_body}"
    routing_category = triage.routing_category or "general"

    tone_profile_block, tone_references = await tone_profile_service.load_for_draft(
        session,
        openai_client=openai_client,
        settings=settings,
        mailbox=mailbox,
        routing_category=routing_category,
        email_text=email_text,
    )
    negative_constraints = await rejection_memory_service.find_negative_constraints(
        session,
        openai_client=openai_client,
        settings=settings,
        email_text=email_text,
        mailbox=mailbox,
        routing_category=routing_category,
        limit=3,
    )
    urgency_hints = await urgency_feedback_service.find_urgency_hints(
        session,
        openai_client=openai_client,
        settings=settings,
        email_text=email_text,
        mailbox=mailbox,
        routing_category=routing_category,
        limit=3,
    )

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
            mailbox,
            thread_context,
            current=email,
        )

    if session.in_transaction():
        await session.commit()

    reference_loader = None
    if skill_ids:
        from app.services.skill_reference_service import make_reference_loader

        reference_loader, _ = make_reference_loader(active_skill_ids=set(skill_ids))

    instruction_text = (instruction or "").strip() or None
    result = await draft_llm.generate_draft(
        email,
        thread_context,
        triage,
        client=client,
        settings=settings,
        skills=active_skills_contents,
        tone_references=tone_references,
        tone_profile=tone_profile_block,
        negative_constraints=negative_constraints,
        urgency_hints=urgency_hints,
        instruction=instruction_text,
        reference_loader=reference_loader,
        confirmed_associations=confirmed_associations,
        directory=directory,
    )

    regen_message_id = f"{latest_graph_id}:regen:{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
    async with session.begin():
        persisted = await draft_repo.create_regenerated_draft(
            session,
            thread_id=thread_id,
            message_id=regen_message_id,
            draft=result.draft,
            tool_calls=result.tool_calls or None,
            applied_skills=applied_skills or None,
        )

        audit_payload: dict[str, object] = {
            "draft_id": str(persisted.id),
            "thread_id": str(thread_id),
            "prompt_version": result.prompt_version,
            "tool_calls": result.tool_calls,
            "force_letter": force_letter,
        }
        if instruction_text:
            audit_payload["instruction_preview"] = instruction_text[:120]

        try:
            await audit_service.log_event(
                session,
                event_type=audit_event,
                conversation_id=conversation_id,
                mailbox=mailbox,
                payload=audit_payload,
                actor=actor,
            )
        except Exception:
            logger.warning(
                "draft_regenerated_audit_failed",
                thread_id=str(thread_id),
                draft_id=str(persisted.id),
                audit_event=audit_event,
            )

    logger.info(
        audit_event.replace(".", "_"),
        thread_id=str(thread_id),
        draft_id=str(persisted.id),
        prompt_version=result.prompt_version,
        actor=actor,
        force_letter=force_letter,
    )
    return persisted


async def generate_draft(
    session: AsyncSession,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    thread_id: uuid.UUID,
    actor: str = "user",
    openai_client: AsyncOpenAI | None = None,
    graph_client: object | None = None,
) -> DraftResponseSchema:
    """Force a letter draft on a briefing thread (reviewer-initiated)."""
    return await regenerate_draft(
        session,
        client=client,
        settings=settings,
        thread_id=thread_id,
        instruction=None,
        actor=actor,
        openai_client=openai_client,
        force_letter=True,
        audit_event="draft.generated",
        graph_client=graph_client,
    )
