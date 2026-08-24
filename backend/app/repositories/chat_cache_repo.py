"""Semantic InboxAssistant response cache (pgvector cosine)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.chat_response_cache import ChatResponseCache


@dataclass(frozen=True, slots=True)
class ChatCacheHit:
    id: uuid.UUID
    response_json: dict
    similarity: float
    citation_thread_ids: list[uuid.UUID]


def normalize_chat_query(message: str) -> str:
    return " ".join((message or "").lower().split())


def scope_key(mailbox: str | None, *, mailbox_list: Sequence[str] = ()) -> str:
    """Cache isolation key. Empty string is never a shared 'all mailboxes' bucket.

    When the reviewer did not scope the ask, key off a hash of the current
    TARGET_MAILBOXES allowlist so a config change cannot inherit stale rows.
    """
    scoped = (mailbox or "").strip().lower()
    if scoped:
        return scoped
    allowlist = ",".join(
        sorted(item.strip().lower() for item in mailbox_list if item and item.strip())
    )
    digest = hashlib.sha256(allowlist.encode("utf-8")).hexdigest()[:16]
    return f"all:{digest}"


async def find_semantic_hit(
    session: AsyncSession,
    *,
    mailbox_key: str,
    user_key: str,
    query_embedding: list[float],
    similarity_threshold: float,
) -> ChatCacheHit | None:
    now = datetime.now(UTC)
    distance = ChatResponseCache.query_embedding.cosine_distance(query_embedding)
    similarity = (1 - distance).label("similarity_score")
    stmt = (
        select(ChatResponseCache, similarity)
        .where(ChatResponseCache.mailbox_key == mailbox_key)
        .where(ChatResponseCache.user_key == user_key)
        .where(ChatResponseCache.expires_at > now)
        .where(distance <= (1.0 - similarity_threshold))
        .order_by(distance)
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    cache_row, score = row
    return ChatCacheHit(
        id=cache_row.id,
        response_json=dict(cache_row.response_json),
        similarity=float(score),
        citation_thread_ids=list(cache_row.citation_thread_ids or []),
    )


async def record_semantic_hit(session: AsyncSession, cache_id: uuid.UUID) -> None:
    """Increment ``hits`` off the lookup path so a SELECT never takes a row lock."""
    await session.execute(
        update(ChatResponseCache)
        .where(ChatResponseCache.id == cache_id)
        .values(hits=ChatResponseCache.hits + 1)
    )


async def store(
    session: AsyncSession,
    *,
    mailbox_key: str,
    user_key: str,
    query_normalized: str,
    query_embedding: list[float],
    response_json: dict,
    citation_thread_ids: list[uuid.UUID],
    expires_at: datetime,
) -> uuid.UUID:
    row = ChatResponseCache(
        mailbox_key=mailbox_key,
        user_key=user_key,
        query_normalized=query_normalized,
        query_embedding=query_embedding,
        response_json=response_json,
        citation_thread_ids=list(citation_thread_ids),
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return row.id


async def invalidate_for_mailbox(session: AsyncSession, mailbox_key: str) -> int:
    result = await session.execute(
        delete(ChatResponseCache).where(ChatResponseCache.mailbox_key == mailbox_key)
    )
    return int(result.rowcount or 0)


async def invalidate_for_threads(
    session: AsyncSession,
    thread_ids: list[uuid.UUID],
) -> int:
    if not thread_ids:
        return 0
    result = await session.execute(
        delete(ChatResponseCache).where(ChatResponseCache.citation_thread_ids.overlap(thread_ids))
    )
    return int(result.rowcount or 0)


async def purge_expired(session: AsyncSession) -> int:
    result = await session.execute(
        delete(ChatResponseCache).where(ChatResponseCache.expires_at <= func.now())
    )
    return int(result.rowcount or 0)
