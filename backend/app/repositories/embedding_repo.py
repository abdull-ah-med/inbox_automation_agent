"""Email embedding repository — insert + thresholded pgvector cosine search."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.email_embedding import EmailEmbedding
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.repositories import message_repo

# Cosine distance (<=>) → similarity = 1 - distance (pgvector docs / OpenAI FAQ).


async def insert_embedding(
    session: AsyncSession,
    *,
    embedding: list[float],
    mailbox: str,
    conversation_id: str,
    sender_email: str,
    recipient_emails: list[str],
    cc_emails: list[str],
    sent_at: datetime,
    body_preview: str,
    graph_message_id: str | None = None,
    message_pk: uuid.UUID | None = None,
) -> EmbeddingMatchSchema:
    """Insert an embedding row; skip duplicate when ``message_id`` already exists.

    Resolves ``graph_message_id`` → ``messages.id`` when ``message_pk`` is omitted.
    Uses ``ON CONFLICT DO NOTHING`` on the partial unique index for race safety.
    """
    resolved_pk = message_pk
    if resolved_pk is None and graph_message_id:
        existing_msg = await message_repo.get_by_graph_id(session, graph_message_id)
        if existing_msg is not None:
            resolved_pk = existing_msg.id

    if resolved_pk is not None:
        existing_stmt = select(EmailEmbedding).where(EmailEmbedding.message_id == resolved_pk)
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            return EmbeddingMatchSchema(
                id=existing.id,
                conversation_id=existing.conversation_id,
                similarity_score=1.0,
                message_id=existing.message_id,
            )

    values = {
        "message_id": resolved_pk,
        "mailbox": mailbox,
        "conversation_id": conversation_id,
        "sender_email": sender_email,
        "recipient_emails": list(recipient_emails),
        "cc_emails": list(cc_emails),
        "embedding": embedding,
        "sent_at": sent_at,
        "body_preview": body_preview,
    }

    if resolved_pk is not None:
        # Partial unique index uq_email_embeddings_message_id (message_id IS NOT NULL).
        stmt = (
            pg_insert(EmailEmbedding)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["message_id"],
                index_where=text("message_id IS NOT NULL"),
            )
            .returning(EmailEmbedding.id, EmailEmbedding.conversation_id, EmailEmbedding.message_id)
        )
        result = await session.execute(stmt)
        inserted = result.one_or_none()
        if inserted is not None:
            await session.flush()
            return EmbeddingMatchSchema(
                id=inserted.id,
                conversation_id=inserted.conversation_id,
                similarity_score=1.0,
                message_id=inserted.message_id,
            )
        # Concurrent insert won — return the existing row.
        existing = (
            await session.execute(
                select(EmailEmbedding).where(EmailEmbedding.message_id == resolved_pk)
            )
        ).scalar_one()
        return EmbeddingMatchSchema(
            id=existing.id,
            conversation_id=existing.conversation_id,
            similarity_score=1.0,
            message_id=existing.message_id,
        )

    row = EmailEmbedding(**values)
    session.add(row)
    await session.flush()
    return EmbeddingMatchSchema(
        id=row.id,
        conversation_id=row.conversation_id,
        similarity_score=1.0,
        message_id=row.message_id,
    )


async def search_similar(
    session: AsyncSession,
    *,
    embedding: list[float],
    min_similarity: float,
    top_k: int,
    exclude_conversation_id: str | None = None,
) -> list[EmbeddingMatchSchema]:
    """Return top-K cosine matches at or above ``min_similarity``.

    Filters by distance in SQL (``<=> <= 1 - min_similarity``) before LIMIT so
    thresholding does not shrink the candidate window in application code.
    Uses ``vector_cosine_ops`` / ``cosine_distance`` so the HNSW index applies.
    See https://github.com/pgvector/pgvector#querying
    """
    max_distance = 1.0 - min_similarity
    distance = EmailEmbedding.embedding.cosine_distance(embedding)
    similarity = (1 - distance).label("similarity_score")
    stmt = (
        select(EmailEmbedding, similarity)
        .where(distance <= max_distance)
        .order_by(distance)
        .limit(top_k)
    )
    if exclude_conversation_id is not None:
        stmt = stmt.where(EmailEmbedding.conversation_id != exclude_conversation_id)

    result = await session.execute(stmt)
    matches: list[EmbeddingMatchSchema] = []
    for row, score in result.all():
        matches.append(
            EmbeddingMatchSchema(
                id=row.id,
                conversation_id=row.conversation_id,
                similarity_score=float(score),
                message_id=row.message_id,
            )
        )
    return matches
