"""Reviewer override: this thread is not spam. Local DB only — never moves Outlook mail."""

from __future__ import annotations

import uuid

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ThreadNotFoundError, ThreadStateError
from app.core.internal_mail import extract_email_address
from app.models.schemas.email import ThreadStateEnum
from app.models.schemas.spam import NotSpamResponseSchema
from app.repositories import message_repo, spam_allowlist_repo, thread_repo
from app.services import audit_service, ingestion_service, pipeline_service

logger = structlog.get_logger(__name__)


async def mark_not_spam(
    session: AsyncSession,
    *,
    settings: Settings,
    thread_id: uuid.UUID,
    actor: str,
    redis: Redis,
    client: AsyncAnthropic,
    openai_client: AsyncOpenAI | None = None,
) -> NotSpamResponseSchema:
    """Allowlist the inbound sender, un-hide the thread, and re-run triage/draft.

    Does not call Graph write APIs. If the message lives in Outlook Junk, it stays there.
    """
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise ThreadNotFoundError(f"Thread not found: {thread_id}")
    if thread.state != ThreadStateEnum.SPAM.value:
        raise ThreadStateError("Thread is not SPAM")

    messages = await message_repo.list_by_thread(session, thread.id)
    inbound = next(
        (msg for msg in reversed(messages) if msg.direction.lower() == "inbound"),
        None,
    )
    if inbound is None:
        raise ThreadStateError("SPAM thread has no inbound sender to allowlist")
    sender_address = extract_email_address(inbound.sender)
    if sender_address is None:
        raise ThreadStateError("SPAM thread has no inbound sender to allowlist")

    await spam_allowlist_repo.upsert(
        session,
        mailbox=thread.mailbox,
        sender_address=sender_address,
        thread_id=thread.id,
        actor=actor,
    )
    await audit_service.log_event(
        session,
        event_type="review.not_spam",
        conversation_id=thread.conversation_id,
        mailbox=thread.mailbox,
        payload={
            "thread_id": str(thread.id),
            "outlook_unchanged": True,
        },
        actor=actor,
    )
    await thread_repo.set_thread_outcome(
        session,
        thread.id,
        state=ThreadStateEnum.NEW.value,
    )
    try:
        from app.repositories import chat_cache_repo

        await chat_cache_repo.invalidate_for_threads(session, [thread.id])
        await chat_cache_repo.invalidate_for_mailbox(session, thread.mailbox.strip().lower())
    except Exception:
        logger.warning("not_spam_chat_cache_invalidate_failed", thread_id=str(thread.id))
    await session.commit()

    ingest_result = await ingestion_service.build_thread_context_from_db(
        session,
        mailbox=thread.mailbox,
        message_id=inbound.graph_message_id,
    )
    if ingest_result is None:
        raise ThreadStateError("SPAM thread has no inbound sender to allowlist")

    try:
        await pipeline_service.run_phased_after_ingest(
            redis=redis,
            settings=settings,
            ingest_result=ingest_result,
            client=client,
            openai_client=openai_client,
            post_slack=True,
        )
    except Exception:
        logger.exception(
            "not_spam_retriage_failed",
            thread_id=str(thread.id),
            conversation_id=thread.conversation_id,
        )
        await thread_repo.set_thread_outcome(
            session,
            thread.id,
            state=ThreadStateEnum.REQUIRES_HUMAN.value,
        )
        await session.commit()

    refreshed = await thread_repo.get_by_id(session, thread.id)
    new_state = refreshed.state if refreshed is not None else ThreadStateEnum.NEW.value
    return NotSpamResponseSchema(
        thread_id=thread.id,
        state=new_state,
        is_spam=False,
        sender_address=sender_address,
        outlook_unchanged=True,
    )
