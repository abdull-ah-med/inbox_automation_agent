"""Reply embedding repository — approved reply memory for tone RAG."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
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


async def store_reply_embedding(
    session: AsyncSession,
    *,
    draft_id: uuid.UUID,
    mailbox: str,
    embedding: list[float],
    reply_text: str,
    email_preview: str | None = None,
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


async def find_similar_replies(
    session: AsyncSession,
    *,
    query_embedding: list[float],
    mailbox: str | None = None,
    limit: int = 3,
) -> list[str]:
    """Return top-N reply texts by cosine similarity (empty list if none)."""
    if limit < 1:
        return []

    distance = ReplyEmbedding.embedding.cosine_distance(query_embedding)
    stmt = select(ReplyEmbedding.reply_text, distance.label("distance")).order_by(distance)
    if mailbox is not None:
        stmt = stmt.where(ReplyEmbedding.mailbox == mailbox)
    stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    return [row.reply_text for row in result.all() if row.reply_text]
