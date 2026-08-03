"""Rejection memory — store on reject, retrieve as negative constraints at draft time."""

from __future__ import annotations

import uuid

import structlog
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.pii_redact import scrub_text
from app.repositories import rejection_memory_repo
from app.services import embedding_service

logger = structlog.get_logger(__name__)


async def store_rejection(
    session: AsyncSession,
    *,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    draft_id: uuid.UUID,
    mailbox: str,
    routing_category: str | None,
    reason_code: str,
    note: str,
) -> uuid.UUID | None:
    """Embed and store a rejection. Best-effort — never raises. Returns memory id."""
    if openai_client is None or not settings.openai_api_key.strip():
        logger.info(
            "rejection_memory_store_skipped_no_openai",
            draft_id=str(draft_id),
            mailbox=mailbox,
        )
        return None

    category = routing_category or "general"
    scrubbed_note = scrub_text(note)
    if not scrubbed_note.strip():
        return None

    embed_input = f"{reason_code}: {scrubbed_note}"
    try:
        vector = await embedding_service.embed_text(
            embed_input,
            client=openai_client,
            settings=settings,
        )
    except Exception:
        logger.exception(
            "rejection_memory_store_failed",
            draft_id=str(draft_id),
            mailbox=mailbox,
        )
        return None

    try:
        stored = await rejection_memory_repo.store_rejection_memory(
            session,
            draft_id=draft_id,
            mailbox=mailbox,
            routing_category=category,
            reason_code=reason_code,
            note=scrubbed_note,
            embedding=vector,
        )
        logger.info(
            "rejection_memory_stored",
            draft_id=str(draft_id),
            mailbox=mailbox,
            reason_code=reason_code,
        )
        return stored.id
    except Exception:
        if session.in_transaction():
            await session.rollback()
        logger.exception(
            "rejection_memory_store_failed",
            draft_id=str(draft_id),
            mailbox=mailbox,
        )
        return None


async def find_negative_constraints(
    session: AsyncSession,
    *,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    email_text: str,
    mailbox: str,
    routing_category: str | None,
    limit: int = 3,
) -> list[str]:
    """Retrieve top negative constraints. Returns [] on any failure."""
    if openai_client is None or not settings.openai_api_key.strip():
        return []

    category = routing_category or "general"
    try:
        recent = await rejection_memory_repo.list_recent_for_category(
            session,
            mailbox=mailbox,
            routing_category=category,
            limit=limit + 1,
        )
        if len(recent) <= limit:
            return [
                rejection_memory_repo.format_constraint_line(
                    reason_code=row.reason_code,
                    note=row.note,
                )
                for row in recent[:limit]
            ]

        scrubbed = scrub_text(email_text)
        if not scrubbed.strip():
            return [
                rejection_memory_repo.format_constraint_line(
                    reason_code=row.reason_code,
                    note=row.note,
                )
                for row in recent[:limit]
            ]

        vector = await embedding_service.embed_text(
            scrubbed,
            client=openai_client,
            settings=settings,
        )
        return await rejection_memory_repo.find_similar_constraints(
            session,
            query_embedding=vector,
            mailbox=mailbox,
            routing_category=category,
            limit=limit,
        )
    except Exception:
        logger.exception(
            "rejection_memory_find_failed",
            mailbox=mailbox,
            routing_category=category,
        )
        return []
