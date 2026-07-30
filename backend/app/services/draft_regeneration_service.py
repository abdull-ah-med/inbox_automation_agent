"""Draft regeneration — re-run Sonnet with a reviewer instruction override."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import DraftGenerationError, ThreadNotFoundError
from app.llm import draft_generator as draft_llm
from app.models.schemas.classification import TriageResultSchema
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
    skill_repo,
    thread_repo,
)
from app.services import audit_service, reply_memory_service

logger = structlog.get_logger(__name__)


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
        subject=subject,
        body_text=message.body_text,
        body_preview=message.body_preview,
        received_at=message.received_at,
        direction=direction,
        to_recipients=list(message.to_recipients),
        cc_recipients=list(message.cc_recipients),
        has_attachments=bool(message.has_attachments),
    )


async def regenerate_draft(
    session: AsyncSession,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    thread_id: uuid.UUID,
    instruction: str,
    actor: str = "user",
    openai_client: AsyncOpenAI | None = None,
) -> DraftResponseSchema:
    """Create a NEW draft for a thread using a reviewer instruction override.

    Does not update or delete prior drafts. Does not send email.

    Reads + tone-ref lookup run first; the session transaction is committed
    before the Sonnet call so Postgres is not held for LLM latency. Persist
    + audit run in a short write transaction afterward.
    """
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None:
        raise ThreadNotFoundError(f"Thread not found: {thread_id}")
    if not settings.mailbox_allowed(thread.mailbox):
        raise ThreadNotFoundError(f"Thread not found: {thread_id}")

    messages = await message_repo.list_by_thread(session, thread_id)
    if not messages:
        raise DraftGenerationError("Cannot regenerate draft: thread has no messages")

    latest = messages[-1]
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
    if triage_flags is None:
        triage = TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary=None,
            needs_context=False,
        )
    else:
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
        )

    active_skills = await skill_repo.list_active(session)
    skill_contents = [skill.content for skill in active_skills]

    # Snapshot values needed after we release the DB transaction.
    mailbox = thread.mailbox
    conversation_id = thread.conversation_id
    subject = thread.subject
    latest_body = latest.body_text
    latest_graph_id = latest.graph_message_id

    # Release any open transaction before OpenAI embed + Sonnet.
    if session.in_transaction():
        await session.commit()

    tone_references = await reply_memory_service.find_similar_replies(
        session,
        openai_client=openai_client,
        settings=settings,
        email_text=f"{subject}\n\n{latest_body}",
        mailbox=mailbox,
        limit=3,
    )
    if session.in_transaction():
        await session.commit()

    result = await draft_llm.generate_draft(
        email,
        thread_context,
        triage,
        client=client,
        settings=settings,
        skills=skill_contents,
        tone_references=tone_references,
        instruction=instruction,
    )

    regen_message_id = f"{latest_graph_id}:regen:{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
    async with session.begin():
        persisted = await draft_repo.create_regenerated_draft(
            session,
            thread_id=thread_id,
            message_id=regen_message_id,
            draft=result.draft,
        )

        try:
            await audit_service.log_event(
                session,
                event_type="draft.regenerated",
                conversation_id=conversation_id,
                mailbox=mailbox,
                payload={
                    "draft_id": str(persisted.id),
                    "thread_id": str(thread_id),
                    "instruction_preview": instruction[:120],
                    "prompt_version": result.prompt_version,
                },
                actor=actor,
            )
        except Exception:
            logger.warning(
                "draft_regenerated_audit_failed",
                thread_id=str(thread_id),
                draft_id=str(persisted.id),
            )

    logger.info(
        "draft_regenerated",
        thread_id=str(thread_id),
        draft_id=str(persisted.id),
        prompt_version=result.prompt_version,
        actor=actor,
    )
    return persisted
