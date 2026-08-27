"""Catch-up triage and learning when a human already replied in Outlook."""

from __future__ import annotations

import uuid

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.schemas.draft import DraftResponseSchema, DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import EmailTriageState
from app.repositories import (
    audit_repo,
    draft_repo,
    message_repo,
    sent_reply_repo,
    thread_repo,
)
from app.repositories.sent_reply_repo import SentReplySchema
from app.services import draft_feedback_service

logger = structlog.get_logger(__name__)

LEARNED_FROM_OUTLOOK_NOTE = "Learned from Outlook send"


async def thread_needs_catchup_triage(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    mailbox: str,
    conversation_id: str,
) -> bool:
    """True when a human reply exists, inbound exists, and triage never ran."""
    sent = await sent_reply_repo.get_by_thread(session, thread_id)
    if sent is None:
        return False
    flags = await audit_repo.get_latest_triage_flags(
        session,
        conversation_id,
        mailbox=mailbox,
    )
    if flags is not None:
        return False
    messages = await message_repo.list_by_thread(session, thread_id)
    return any(getattr(m, "direction", None) == "inbound" for m in messages)


def select_latest_inbound(
    thread_context: ThreadContextSchema,
) -> EmailMessageSchema | None:
    """Pick the chronologically latest inbound message for catch-up triage."""
    inbounds = [m for m in thread_context.messages if m.direction == EmailDirectionEnum.INBOUND]
    if not inbounds:
        return None
    return max(inbounds, key=lambda m: m.received_at)


async def promote_sent_reply_as_approved(
    session: AsyncSession,
    *,
    sent_reply: SentReplySchema,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    routing_category: str | None = None,
    anthropic_client: AsyncAnthropic | None = None,
) -> DraftResponseSchema | None:
    """Create/approve a synthetic draft from the sent body.

    Idempotent when ``sent_reply.draft_id`` already points at an approved draft.
    Caller must commit, then call ``store_promoted_reply_memory`` (same split as
    HTTP approve → ``store_approved_reply_memory``).
    """
    _ = settings
    _ = openai_client
    _ = anthropic_client

    if sent_reply.draft_id is not None:
        existing = await draft_repo.get_draft_by_id(session, sent_reply.draft_id)
        if (
            existing is not None
            and existing.approved_at is not None
            and existing.feedback_action == "approve"
        ):
            return existing

    outbound_msg = await message_repo.get_by_id_trusted(session, sent_reply.message_id)
    if outbound_msg is None:
        logger.warning(
            "sent_reply_promote_missing_message",
            sent_reply_id=str(sent_reply.id),
            message_id=str(sent_reply.message_id),
        )
        return None

    thread = await thread_repo.get_by_id_trusted(session, sent_reply.thread_id)
    if thread is None:
        logger.warning(
            "sent_reply_promote_missing_thread",
            thread_id=str(sent_reply.thread_id),
        )
        return None

    subject = thread.subject
    subject_line = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    draft_schema = DraftSchema(
        subject_line=subject_line,
        reply_body=sent_reply.sent_body_snapshot,
        teaching_note=LEARNED_FROM_OUTLOOK_NOTE,
        urgency="NORMAL",
        urgency_reason="Human already replied in Outlook",
    )
    created = await draft_repo.create_draft(
        session,
        thread_id=sent_reply.thread_id,
        message_id=outbound_msg.graph_message_id,
        draft=draft_schema,
        prompt_version="sent-reply-catchup",
        routing_category=routing_category,
    )
    approved = await draft_feedback_service.approve_draft(
        session,
        created.id,
        approval_note=LEARNED_FROM_OUTLOOK_NOTE,
        approval_scope="similar",
        actor="system",
        settings=None,
    )
    await sent_reply_repo.link_draft(
        session,
        sent_reply_id=sent_reply.id,
        draft_id=approved.id,
        matched_by="approved_draft",
    )
    return approved


async def store_promoted_reply_memory(
    *,
    draft: DraftResponseSchema,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    anthropic_client: AsyncAnthropic | None = None,
) -> None:
    """Post-commit reply memory for a promoted sent reply (best-effort)."""
    await draft_feedback_service.store_approved_reply_memory(
        draft=draft,
        settings=settings,
        openai_client=openai_client,
        anthropic_client=anthropic_client,
    )


async def run_catchup_after_outbound(
    *,
    redis: Redis,
    settings: Settings,
    thread_id: uuid.UUID,
    mailbox: str,
    conversation_id: str,
    outbound_graph_message_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    openai_client: AsyncOpenAI | None = None,
    anthropic_client: AsyncAnthropic | None = None,
    graph_client: object | None = None,
) -> EmailTriageState | None:
    """Run triage-only on latest inbound, promote sent reply, keep RESOLVED.

    Never raises to callers — ingest already succeeded. Returns None when
    catch-up is not needed or inputs are insufficient.
    """
    _ = redis
    _ = graph_client
    try:
        from app.core.dependencies import (
            anthropic_client_from_settings,
            openai_client_from_settings,
        )
        from app.models.schemas.email import ThreadStateEnum
        from app.services import ingestion_service
        from app.services.pipeline import service as pipeline_service

        async with session_factory() as session:
            thread = await thread_repo.get_by_id_trusted(session, thread_id)
            thread_mailbox = thread.mailbox if thread is not None else mailbox
            needs = await thread_needs_catchup_triage(
                session,
                thread_id=thread_id,
                mailbox=thread_mailbox,
                conversation_id=conversation_id,
            )
            if session.in_transaction():
                await session.commit()
        if not needs:
            return None

        async with session_factory() as session:
            rebuilt = await ingestion_service.build_thread_context_from_db(
                session,
                mailbox=thread_mailbox,
                message_id=outbound_graph_message_id,
            )
            if session.in_transaction():
                await session.commit()

        if rebuilt is None or rebuilt.thread_context is None:
            logger.info(
                "sent_reply_catchup_no_thread_context",
                thread_id=str(thread_id),
                mailbox=mailbox,
            )
            return None

        inbound = select_latest_inbound(rebuilt.thread_context)
        if inbound is None:
            logger.info(
                "sent_reply_catchup_no_inbound",
                thread_id=str(thread_id),
                mailbox=mailbox,
            )
            return None

        client = anthropic_client or anthropic_client_from_settings(settings)
        resolved_openai = openai_client or openai_client_from_settings(settings)

        state = EmailTriageState(
            original_email=inbound,
            thread_context=rebuilt.thread_context,
            draft_status="PENDING",
        )
        state = await pipeline_service._phased_triage_summarize(
            state=state,
            email=inbound,
            client=client,
            settings=settings,
            session_factory=session_factory,
            parsed_thread_id=thread_id,
            apply_thread_state=False,
        )

        state.draft_status = "SKIPPED"
        state.slack_delivery = "not_required"
        await pipeline_service._embed_in_fresh_session(
            state=state,
            openai_client=resolved_openai,
            settings=settings,
            session_factory=session_factory,
        )

        async with session_factory() as session, session.begin():
            await pipeline_service._safe_audit(
                session,
                state=state,
                event_type="draft.skipped_already_replied",
                payload={
                    "draft_status": state.draft_status,
                    "reason": "already_replied",
                },
            )

        routing = state.triage.routing_category if state.triage is not None else None
        approved: DraftResponseSchema | None = None
        async with session_factory() as session, session.begin():
            sent = await sent_reply_repo.get_by_thread(session, thread_id)
            if sent is not None:
                approved = await promote_sent_reply_as_approved(
                    session,
                    sent_reply=sent,
                    settings=settings,
                    openai_client=resolved_openai,
                    routing_category=routing,
                    anthropic_client=client,
                )
            await thread_repo.set_thread_outcome(
                session,
                thread_id,
                state=ThreadStateEnum.RESOLVED.value,
            )

        if approved is not None:
            await store_promoted_reply_memory(
                draft=approved,
                settings=settings,
                openai_client=resolved_openai,
                anthropic_client=client,
            )

        logger.info(
            "sent_reply_catchup_complete",
            thread_id=str(thread_id),
            mailbox=mailbox,
            draft_status=state.draft_status,
            promoted=approved is not None,
        )
        return state
    except Exception:
        logger.exception(
            "sent_reply_catchup_failed",
            thread_id=str(thread_id),
            mailbox=mailbox,
        )
        return None
