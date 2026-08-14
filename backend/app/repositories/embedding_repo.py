"""Email embedding repository — insert + hybrid (vector + FTS) search."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.email_embedding import EmailEmbedding
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.repositories import message_repo


def _apply_mailbox_scope(
    stmt: Select[Any],
    *,
    mailbox: str | None,
    mailboxes: Sequence[str] | None,
) -> Select[Any] | None:
    """Apply mailbox equality or IN. ``None`` means skip the query (empty allowlist)."""
    if mailbox is not None:
        return stmt.where(EmailEmbedding.mailbox == mailbox)
    if mailboxes is not None:
        if not mailboxes:
            return None
        return stmt.where(EmailEmbedding.mailbox.in_(list(mailboxes)))
    return stmt


def _match_from_row(row: EmailEmbedding, score: float) -> EmbeddingMatchSchema:
    return EmbeddingMatchSchema(
        id=row.id,
        conversation_id=row.conversation_id,
        similarity_score=score,
        message_id=row.message_id,
        mailbox=row.mailbox,
        body_preview=row.body_preview,
    )


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
    search_document: str = "",
    embed_clean_version: int | None = None,
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
        "search_document": search_document or "",
        "embed_clean_version": embed_clean_version,
    }

    if resolved_pk is not None:
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


async def update_embedding_document(
    session: AsyncSession,
    *,
    embedding_id: uuid.UUID,
    embedding: list[float],
    search_document: str,
    body_preview: str,
    embed_clean_version: int,
) -> None:
    row = (
        await session.execute(select(EmailEmbedding).where(EmailEmbedding.id == embedding_id))
    ).scalar_one_or_none()
    if row is None:
        return
    row.embedding = embedding
    row.search_document = search_document
    row.body_preview = body_preview
    row.embed_clean_version = embed_clean_version
    await session.flush()


async def search_similar(
    session: AsyncSession,
    *,
    embedding: list[float],
    min_similarity: float,
    top_k: int,
    mailbox: str | None = None,
    mailboxes: Sequence[str] | None = None,
    exclude_conversation_id: str | None = None,
) -> list[EmbeddingMatchSchema]:
    """Return top-K cosine matches at or above ``min_similarity``.

    When ``mailbox`` is set, results are scoped to that mailbox so draft/context
    retrieval cannot leak content across TARGET_MAILBOXES. ``mailboxes`` applies
    the same isolation to an allowlist (empty list → no query).
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
    scoped = _apply_mailbox_scope(stmt, mailbox=mailbox, mailboxes=mailboxes)
    if scoped is None:
        return []
    stmt = scoped
    if exclude_conversation_id is not None:
        stmt = stmt.where(EmailEmbedding.conversation_id != exclude_conversation_id)

    result = await session.execute(stmt)
    matches: list[EmbeddingMatchSchema] = []
    for row, score in result.all():
        matches.append(_match_from_row(row, float(score)))
    return matches


async def search_fts(
    session: AsyncSession,
    *,
    query_text: str,
    top_k: int,
    mailbox: str | None = None,
    mailboxes: Sequence[str] | None = None,
    exclude_conversation_id: str | None = None,
) -> list[EmbeddingMatchSchema]:
    """Return top-K full-text matches by ``ts_rank_cd`` on ``search_vector``.

    When ``mailbox`` is set, results are scoped to that mailbox (same isolation
    as ``search_similar``). ``mailboxes`` applies the same isolation to an
    allowlist (empty list → no query).
    """
    from sqlalchemy import column, func, literal_column

    cleaned = (query_text or "").strip()
    if not cleaned:
        return []

    # Generated ``search_vector`` is not an ORM attribute; reference by name.
    search_vector: object = column("search_vector")
    tsquery = func.websearch_to_tsquery("english", cleaned)
    rank = func.ts_rank_cd(search_vector, tsquery).label("rank")
    stmt = (
        select(EmailEmbedding, rank)
        .where(literal_column("search_vector").op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(top_k)
    )
    scoped = _apply_mailbox_scope(stmt, mailbox=mailbox, mailboxes=mailboxes)
    if scoped is None:
        return []
    stmt = scoped
    if exclude_conversation_id is not None:
        stmt = stmt.where(EmailEmbedding.conversation_id != exclude_conversation_id)

    result = await session.execute(stmt)
    matches: list[EmbeddingMatchSchema] = []
    for row, score in result.all():
        # Normalize FTS rank into [0, 1] soft range for schema validation; RRF
        # uses ranks not raw scores so absolute magnitude is unused downstream.
        raw = float(score) if score is not None else 0.0
        matches.append(_match_from_row(row, min(1.0, max(0.0, raw))))
    return matches
