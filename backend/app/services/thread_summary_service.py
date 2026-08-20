"""Generate and refresh thread-level summaries for long conversations."""

from __future__ import annotations

import uuid

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm import thread_summary as thread_summary_llm
from app.repositories import message_repo, thread_summary_repo

logger = structlog.get_logger(__name__)

THREAD_SUMMARY_MIN_MESSAGES = 5


def should_refresh_thread_summary(*, message_count: int, stored_count: int | None) -> bool:
    if message_count < THREAD_SUMMARY_MIN_MESSAGES:
        return False
    if stored_count is None:
        return True
    if message_count == stored_count:
        return False
    return message_count >= stored_count * 2


async def maybe_refresh(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    client: AsyncAnthropic,
    settings: Settings,
) -> thread_summary_repo.ThreadSummarySchema | None:
    messages = await message_repo.list_by_thread(session, thread_id)
    stored = await thread_summary_repo.get(session, thread_id)
    stored_count = stored.message_count if stored is not None else None
    if not should_refresh_thread_summary(
        message_count=len(messages),
        stored_count=stored_count,
    ):
        return stored
    if not messages:
        return stored
    try:
        text = await thread_summary_llm.summarize_thread(
            client=client,
            settings=settings,
            messages=messages,
        )
    except Exception:
        logger.exception("thread_summary_failed", thread_id=str(thread_id))
        return stored
    return await thread_summary_repo.upsert(
        session,
        thread_id=thread_id,
        summary_text=text,
        message_count=len(messages),
        last_message_id=messages[-1].id,
    )


async def maybe_refresh_safe(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    client: AsyncAnthropic,
    settings: Settings,
) -> None:
    try:
        await maybe_refresh(
            session,
            thread_id=thread_id,
            client=client,
            settings=settings,
        )
    except Exception:
        logger.exception("thread_summary_refresh_failed", thread_id=str(thread_id))
