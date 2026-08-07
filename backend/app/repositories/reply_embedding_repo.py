"""Reply embedding repository — approved reply memory for tone RAG."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.reply_embedding import ReplyEmbedding


class ReplyEmbeddingSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    mailbox: str
    reply_text: str
    original_email_preview: str | None = None
    learning_note: str | None = None
    is_excluded: bool = False
    created_at: datetime | None = None


async def store_reply_embedding(
    session: AsyncSession,
    *,
    draft_id: uuid.UUID,
    mailbox: str,
    embedding: list[float],
    reply_text: str,
    email_preview: str | None = None,
    learning_note: str | None = None,
) -> ReplyEmbeddingSchema:
    """Insert reply embedding; idempotent on ``draft_id``."""
    existing_stmt = select(ReplyEmbedding).where(ReplyEmbedding.draft_id == draft_id)
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return ReplyEmbeddingSchema.model_validate(existing)

    stmt = (
        pg_insert(ReplyEmbedding)
        .values(
            draft_id=draft_id,
            mailbox=mailbox,
            embedding=embedding,
            reply_text=reply_text,
            original_email_preview=email_preview,
            learning_note=learning_note,
        )
        .on_conflict_do_nothing(index_elements=["draft_id"])
        .returning(ReplyEmbedding)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        await session.flush()
        return ReplyEmbeddingSchema.model_validate(row)

    existing = (
        await session.execute(select(ReplyEmbedding).where(ReplyEmbedding.draft_id == draft_id))
    ).scalar_one()
    return ReplyEmbeddingSchema.model_validate(existing)


async def list_reply_embeddings(
    session: AsyncSession,
    *,
    mailbox: str | None = None,
    limit: int = 100,
) -> list[ReplyEmbeddingSchema]:
    """List approved replies newest-first for Settings (includes excluded)."""
    capped = max(1, min(limit, 500))
    stmt = select(ReplyEmbedding).order_by(ReplyEmbedding.created_at.desc()).limit(capped)
    if mailbox is not None:
        stmt = stmt.where(ReplyEmbedding.mailbox == mailbox)
    result = await session.execute(stmt)
    return [ReplyEmbeddingSchema.model_validate(row) for row in result.scalars().all()]


async def list_recent_replies(
    session: AsyncSession,
    *,
    mailbox: str,
    limit: int = 3,
) -> list[str]:
    """Newest non-excluded reply texts for a mailbox (few-shot examples)."""
    capped = max(1, min(limit, 20))
    stmt = (
        select(ReplyEmbedding.reply_text)
        .where(
            ReplyEmbedding.mailbox == mailbox,
            ReplyEmbedding.is_excluded.is_(False),
        )
        .order_by(ReplyEmbedding.created_at.desc())
        .limit(capped)
    )
    result = await session.execute(stmt)
    return [text for text in result.scalars().all() if text]


async def set_excluded(
    session: AsyncSession,
    reply_id: uuid.UUID,
    *,
    is_excluded: bool,
) -> ReplyEmbeddingSchema | None:
    """Toggle exclusion from tone RAG. Returns None if the row is missing."""
    stmt = (
        sa_update(ReplyEmbedding)
        .where(ReplyEmbedding.id == reply_id)
        .values(is_excluded=is_excluded)
        .returning(ReplyEmbedding)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return ReplyEmbeddingSchema.model_validate(row)


async def find_similar_replies(
    session: AsyncSession,
    *,
    query_embedding: list[float],
    mailbox: str | None = None,
    limit: int = 3,
) -> list[str]:
    """Return top-N non-excluded reply texts by cosine similarity."""
    if limit < 1:
        return []

    distance = ReplyEmbedding.embedding.cosine_distance(query_embedding)
    stmt = (
        select(ReplyEmbedding.reply_text, distance.label("distance"))
        .where(ReplyEmbedding.is_excluded.is_(False))
        .order_by(distance)
    )
    if mailbox is not None:
        stmt = stmt.where(ReplyEmbedding.mailbox == mailbox)
    stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    return [row.reply_text for row in result.all() if row.reply_text]
