"""Persist Haiku per-message summaries for non-spam emails."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import TriageError
from app.llm import message_summary as summary_llm
from app.llm.email_clean import CLEAN_VERSION
from app.models.schemas.email import EmailMessageSchema
from app.repositories import message_repo

logger = structlog.get_logger(__name__)

_SIBLING_CONCURRENCY = 3


def summary_is_stale(
    *,
    summarized_at: datetime | None,
    summary_clean_version: int | None,
    body_clean_version: int | None,
) -> bool:
    if summarized_at is None:
        return True
    if body_clean_version is None:
        return summary_clean_version != CLEAN_VERSION
    return summary_clean_version is None or summary_clean_version != body_clean_version


async def summarize_and_store(
    session: AsyncSession,
    *,
    email: EmailMessageSchema,
    client: AsyncAnthropic,
    settings: Settings,
    message_pk: object | None = None,
    body_clean_version: int | None = None,
) -> bool:
    """Summarize one email and persist; return True on success.

    Skips short bodies. Raises ``TriageError`` only for API failures after retry —
    callers that must not fail the pipeline should use ``summarize_and_store_safe``.
    """
    if not summary_llm.should_summarize_body(email):
        return False

    result = await summary_llm.summarize_message(
        client=client,
        settings=settings,
        email=email,
    )
    pk = message_pk
    if pk is None:
        existing = await message_repo.get_by_graph_id(session, email.message_id)
        if existing is None:
            logger.warning(
                "message_summary_skip_missing_row",
                message_id=email.message_id,
            )
            return False
        pk = existing.id
        if body_clean_version is None:
            body_clean_version = existing.body_clean_version

    clean_version = body_clean_version if body_clean_version is not None else CLEAN_VERSION
    await message_repo.update_summary(
        session,
        message_id=pk,  # type: ignore[arg-type]
        summary_json=result.summary.model_dump(),
        summary_one_line=result.summary.one_line,
        summarized_at=datetime.now(UTC),
        summary_model=result.model,
        summary_clean_version=clean_version,
    )
    email.summary_one_line = result.summary.one_line
    email.summary_json = result.summary.model_dump()
    return True


async def summarize_and_store_safe(
    session: AsyncSession,
    *,
    email: EmailMessageSchema,
    client: AsyncAnthropic,
    settings: Settings,
    message_pk: object | None = None,
    body_clean_version: int | None = None,
) -> bool:
    """Best-effort summarize; log and return False on failure."""
    try:
        return await summarize_and_store(
            session,
            email=email,
            client=client,
            settings=settings,
            message_pk=message_pk,
            body_clean_version=body_clean_version,
        )
    except TriageError:
        logger.warning(
            "message_summary_skipped",
            message_id=email.message_id,
            exc_info=True,
        )
        return False
    except Exception:
        logger.exception(
            "message_summary_failed",
            message_id=email.message_id,
        )
        return False


async def summarize_current_if_needed(
    session: AsyncSession,
    *,
    email: EmailMessageSchema,
    client: AsyncAnthropic,
    settings: Settings,
) -> None:
    """Summarize the current message when missing or stale."""
    row = await message_repo.get_by_graph_id(session, email.message_id)
    if row is None:
        return
    if not summary_is_stale(
        summarized_at=row.summarized_at,
        summary_clean_version=row.summary_clean_version,
        body_clean_version=row.body_clean_version,
    ):
        email.summary_one_line = row.summary_one_line
        email.summary_json = row.summary_json
        return
    await summarize_and_store_safe(
        session,
        email=email,
        client=client,
        settings=settings,
        message_pk=row.id,
        body_clean_version=row.body_clean_version,
    )


async def backfill_sibling_summaries(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    emails: list[EmailMessageSchema],
    current_message_id: str,
    client: AsyncAnthropic,
    settings: Settings,
) -> None:
    """Best-effort summarize missing siblings (capped concurrency)."""
    siblings = [e for e in emails if e.message_id != current_message_id]
    if not siblings:
        return

    sem = asyncio.Semaphore(_SIBLING_CONCURRENCY)

    async def _one(email: EmailMessageSchema) -> None:
        async with sem, session_factory() as session, session.begin():
            row = await message_repo.get_by_graph_id(session, email.message_id)
            if row is None:
                return
            if not summary_is_stale(
                summarized_at=row.summarized_at,
                summary_clean_version=row.summary_clean_version,
                body_clean_version=row.body_clean_version,
            ):
                email.summary_one_line = row.summary_one_line
                email.summary_json = row.summary_json
                return
            await summarize_and_store_safe(
                session,
                email=email,
                client=client,
                settings=settings,
                message_pk=row.id,
                body_clean_version=row.body_clean_version,
            )

    await asyncio.gather(*[_one(e) for e in siblings], return_exceptions=True)
