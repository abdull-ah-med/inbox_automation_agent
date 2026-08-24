"""Urgency feedback repository — store manual edits, retrieve for draft RAG."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.db.urgency_feedback import UrgencyFeedback
from app.repositories._vector_common import set_hnsw_session_defaults

_REASON_DISPLAY_MAX = 240


class UrgencyFeedbackSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    thread_id: uuid.UUID
    mailbox: str
    routing_category: str | None = None
    previous_urgency: str | None = None
    new_urgency: str
    reason: str
    is_excluded: bool = False
    created_at: datetime | None = None


def format_urgency_hint(*, new_urgency: str, reason: str) -> str:
    text = reason.strip()
    if len(text) > _REASON_DISPLAY_MAX:
        text = text[: _REASON_DISPLAY_MAX - 1] + "…"
    return f"[{new_urgency}] {text}"


async def insert_urgency_feedback(
    session: AsyncSession,
    *,
    draft_id: uuid.UUID,
    thread_id: uuid.UUID,
    mailbox: str,
    routing_category: str | None,
    previous_urgency: str | None,
    new_urgency: str,
    reason: str,
    embedding: list[float],
    edited_by_user_id: uuid.UUID | None = None,
) -> UrgencyFeedbackSchema:
    row = UrgencyFeedback(
        draft_id=draft_id,
        thread_id=thread_id,
        mailbox=mailbox,
        routing_category=routing_category,
        previous_urgency=previous_urgency,
        new_urgency=new_urgency,
        reason=reason,
        embedding=embedding,
        edited_by_user_id=edited_by_user_id,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return UrgencyFeedbackSchema.model_validate(row)


async def find_similar(
    session: AsyncSession,
    *,
    query_embedding: list[float],
    mailbox: str,
    routing_category: str | None = None,
    limit: int = 3,
    min_similarity: float = 0.80,
) -> list[str]:
    """Return formatted urgency hints ordered by cosine similarity.

    ``min_similarity`` maps to cosine distance ``1 - similarity``.
    """
    if limit < 1:
        return []
    await set_hnsw_session_defaults(session, get_settings())
    max_distance = 1.0 - min_similarity
    distance = UrgencyFeedback.embedding.cosine_distance(query_embedding)
    stmt = (
        select(
            UrgencyFeedback.new_urgency,
            UrgencyFeedback.reason,
            distance.label("distance"),
        )
        .where(
            UrgencyFeedback.is_excluded.is_(False),
            UrgencyFeedback.mailbox == mailbox,
            distance <= max_distance,
        )
        .order_by(distance)
        .limit(limit)
    )
    if routing_category is not None:
        stmt = stmt.where(UrgencyFeedback.routing_category == routing_category)
    result = await session.execute(stmt)
    return [
        format_urgency_hint(new_urgency=row.new_urgency, reason=row.reason)
        for row in result.all()
        if row.reason
    ]
