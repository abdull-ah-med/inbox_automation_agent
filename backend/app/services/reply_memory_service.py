"""Approved reply memory — store on approve, retrieve as tone references at draft time."""

from __future__ import annotations

import uuid

import structlog
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.pii_redact import scrub_text
from app.repositories import reply_embedding_repo
from app.services import embedding_service

logger = structlog.get_logger(__name__)


async def store_approved_reply(
    session: AsyncSession,
    *,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    draft_id: uuid.UUID,
    mailbox: str,
    final_body: str,
    email_preview: str | None = None,
) -> None:
    """Embed and store an approved reply. Best-effort — never raises to callers.

    Embedding runs before any DB write so callers can keep the write txn short.
    """
    if openai_client is None or not settings.openai_api_key.strip():
        logger.info(
            "reply_memory_store_skipped_no_openai",
            draft_id=str(draft_id),
            mailbox=mailbox,
        )
        return

    try:
        scrubbed = scrub_text(final_body)
        if not scrubbed.strip():
            return
        # Network call first — do not hold a Postgres transaction open.
        vector = await embedding_service.embed_text(
            scrubbed,
            client=openai_client,
            settings=settings,
        )
    except Exception:
        logger.exception(
            "reply_memory_store_failed",
            draft_id=str(draft_id),
            mailbox=mailbox,
        )
        return

    try:
        await reply_embedding_repo.store_reply_embedding(
            session,
            draft_id=draft_id,
            mailbox=mailbox,
            embedding=vector,
            reply_text=scrubbed,
            email_preview=email_preview,
        )
        logger.info(
            "reply_memory_stored",
            draft_id=str(draft_id),
            mailbox=mailbox,
        )
    except Exception:
        # Flush/DB errors leave the session inactive until rollback
        # (SQLAlchemy 2.0: PendingRollbackError / FAQ session rollback).
        if session.in_transaction():
            await session.rollback()
        logger.exception(
            "reply_memory_store_failed",
            draft_id=str(draft_id),
            mailbox=mailbox,
        )


async def find_similar_replies(
    session: AsyncSession,
    *,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    email_text: str,
    mailbox: str | None = None,
    limit: int = 3,
) -> list[str]:
    """Retrieve similar approved replies. Returns [] on any failure (never raises).

    Embedding runs before the vector query so idle-in-transaction time is minimal.
    """
    if openai_client is None or not settings.openai_api_key.strip():
        return []

    try:
        scrubbed = scrub_text(email_text)
        if not scrubbed.strip():
            return []
        vector = await embedding_service.embed_text(
            scrubbed,
            client=openai_client,
            settings=settings,
        )
    except Exception:
        logger.exception(
            "reply_memory_find_failed",
            mailbox=mailbox,
        )
        return []

    try:
        return await reply_embedding_repo.find_similar_replies(
            session,
            query_embedding=vector,
            mailbox=mailbox,
            limit=limit,
        )
    except Exception:
        logger.exception(
            "reply_memory_find_failed",
            mailbox=mailbox,
        )
        return []
