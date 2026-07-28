"""Classification repository — read helpers for the web dashboard."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.classification import Classification
from app.models.db.message import Message
from app.models.schemas.dashboard import ClassificationView


async def get_latest_for_thread(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> ClassificationView | None:
    """Latest classification across messages in a thread."""
    stmt = (
        select(Classification)
        .join(Message, Message.id == Classification.message_id)
        .where(Message.thread_id == thread_id)
        .order_by(Classification.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return ClassificationView(
        category=row.category,
        intent=row.intent,
        urgency=row.urgency,
        confidence=row.confidence,
        entities=dict(row.entities or {}),
        model_version=row.model_version,
        created_at=row.created_at,
    )
