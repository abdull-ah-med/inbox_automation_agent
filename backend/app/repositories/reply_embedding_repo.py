"""Reply embedding repository — approved reply memory for tone RAG."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.internal_mail import extract_email_address
from app.models.db.draft import Draft
from app.models.db.reply_embedding import ReplyEmbedding
from app.repositories._vector_common import cap_limit, set_hnsw_session_defaults
from app.repositories.memory_list_common import (
    latest_inbound_sender_subquery,
    mailbox_in_allowlist,
    mailbox_matches,
    normalize_mailbox_allowlist,
)
from app.utils.text import truncate_display

_ONE_LINE_MAX = 140


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


class ReplyMemoryListItem(BaseModel):
    """Settings list row — approved reply plus draft context."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    thread_id: uuid.UUID | None = None
    mailbox: str
    reply_text: str
    preview_line: str | None = None
    draft_subject: str | None = None
    sender_email: str | None = None
    receiver_email: str | None = None
    reason_code: str | None = None
    reason_text: str | None = None
    original_email_preview: str | None = None
    learning_note: str | None = None
    is_excluded: bool = False
    created_at: datetime | None = None


def _one_line(text: str | None, *, max_len: int = _ONE_LINE_MAX) -> str | None:
    if not text or not str(text).strip():
        return None
    collapsed = " ".join(str(text).split())
    return truncate_display(collapsed, max_len)


def _sender_email(raw: str | None) -> str | None:
    if not raw or not str(raw).strip():
        return None
    return extract_email_address(str(raw)) or str(raw).strip()


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


def _list_item_from_row(
    embedding: ReplyEmbedding,
    *,
    thread_id: uuid.UUID | None,
    draft_subject: str | None,
    approval_scope: str | None,
    sender_raw: str | None,
) -> ReplyMemoryListItem:
    note = embedding.learning_note
    return ReplyMemoryListItem(
        id=embedding.id,
        draft_id=embedding.draft_id,
        thread_id=thread_id,
        mailbox=embedding.mailbox,
        reply_text=embedding.reply_text,
        preview_line=_one_line(embedding.reply_text),
        draft_subject=draft_subject,
        sender_email=_sender_email(sender_raw),
        receiver_email=embedding.mailbox,
        reason_code=approval_scope,
        reason_text=note,
        original_email_preview=embedding.original_email_preview,
        learning_note=note,
        is_excluded=bool(embedding.is_excluded),
        created_at=embedding.created_at,
    )


async def _list_enriched(
    session: AsyncSession,
    *,
    where_clause: object,
    limit: int,
) -> list[ReplyMemoryListItem]:
    capped = cap_limit(limit, maximum=500)
    inbound = latest_inbound_sender_subquery()
    stmt = (
        select(
            ReplyEmbedding,
            Draft.thread_id,
            Draft.subject,
            Draft.approval_scope,
            inbound.c.sender,
        )
        .outerjoin(Draft, Draft.id == ReplyEmbedding.draft_id)
        .outerjoin(inbound, inbound.c.thread_id == Draft.thread_id)
        .where(where_clause)
        .order_by(ReplyEmbedding.created_at.desc())
        .limit(capped)
    )
    result = await session.execute(stmt)
    return [
        _list_item_from_row(
            embedding,
            thread_id=thread_id,
            draft_subject=subject,
            approval_scope=approval_scope,
            sender_raw=sender,
        )
        for embedding, thread_id, subject, approval_scope, sender in result.all()
    ]


async def list_reply_embeddings(
    session: AsyncSession,
    *,
    mailbox: str,
    limit: int = 100,
) -> list[ReplyMemoryListItem]:
    """List approved replies newest-first for Settings (includes excluded)."""
    if not (mailbox or "").strip():
        raise ValueError("mailbox is required")
    return await _list_enriched(
        session,
        where_clause=mailbox_matches(ReplyEmbedding.mailbox, mailbox),
        limit=limit,
    )


async def list_all_reply_embeddings_admin(
    session: AsyncSession,
    *,
    mailboxes: list[str],
    limit: int = 100,
) -> list[ReplyMemoryListItem]:
    """Cross-mailbox list for admin Settings. Empty allowlist returns no rows."""
    if not normalize_mailbox_allowlist(mailboxes):
        return []
    return await _list_enriched(
        session,
        where_clause=mailbox_in_allowlist(ReplyEmbedding.mailbox, mailboxes),
        limit=limit,
    )


async def get_mailbox_by_id(
    session: AsyncSession,
    reply_id: uuid.UUID,
) -> str | None:
    """Load mailbox for authorization checks without list enrichment joins."""
    stmt = select(ReplyEmbedding.mailbox).where(ReplyEmbedding.id == reply_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_list_item_by_id(
    session: AsyncSession,
    reply_id: uuid.UUID,
) -> ReplyMemoryListItem | None:
    """Load one reply memory row, or None when missing."""
    items = await _list_enriched(
        session,
        where_clause=ReplyEmbedding.id == reply_id,
        limit=1,
    )
    return items[0] if items else None


async def set_excluded(
    session: AsyncSession,
    reply_id: uuid.UUID,
    *,
    is_excluded: bool,
) -> ReplyMemoryListItem | None:
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
    items = await _list_enriched(
        session,
        where_clause=ReplyEmbedding.id == reply_id,
        limit=1,
    )
    return items[0] if items else None


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

    await set_hnsw_session_defaults(session, get_settings())
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
