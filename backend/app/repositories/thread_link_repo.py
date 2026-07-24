"""Thread-link repository — persist cross-thread similarity matches."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.thread_link import ThreadLink


async def insert_thread_link(
    session: AsyncSession,
    *,
    source_message_id: uuid.UUID,
    matched_embedding_id: uuid.UUID,
    matched_conversation_id: str,
    similarity_score: float,
) -> uuid.UUID:
    """Insert a ``thread_links`` row and return its id.

    No free-text rationale — only retrieval metadata.
    """
    row = ThreadLink(
        source_message_id=source_message_id,
        matched_embedding_id=matched_embedding_id,
        matched_conversation_id=matched_conversation_id,
        similarity_score=similarity_score,
    )
    session.add(row)
    await session.flush()
    return row.id
