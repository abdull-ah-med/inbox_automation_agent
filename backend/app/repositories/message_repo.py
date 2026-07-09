"""Message repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.message import Message


class MessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thread_id: uuid.UUID
    graph_message_id: str
    direction: str
    sender: str
    body_text: str
    body_preview: str | None = None
    received_at: datetime


async def get_by_graph_id(
    session: AsyncSession,
    graph_message_id: str,
) -> MessageSchema | None:
    stmt = select(Message).where(Message.graph_message_id == graph_message_id)
    result = await session.execute(stmt)
    message = result.scalar_one_or_none()
    if message is None:
        return None
    return MessageSchema.model_validate(message)


async def list_by_thread(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> list[MessageSchema]:
    stmt = (
        select(Message)
        .where(Message.thread_id == thread_id)
        .order_by(Message.received_at.asc())
    )
    result = await session.execute(stmt)
    return [MessageSchema.model_validate(m) for m in result.scalars().all()]


async def create_message(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    graph_message_id: str,
    direction: str,
    sender: str,
    body_text: str,
    body_preview: str | None,
    received_at: datetime,
) -> MessageSchema:
    existing = await get_by_graph_id(session, graph_message_id)
    if existing is not None:
        return existing

    message = Message(
        thread_id=thread_id,
        graph_message_id=graph_message_id,
        direction=direction,
        sender=sender,
        body_text=body_text,
        body_preview=body_preview,
        received_at=received_at,
    )
    session.add(message)
    await session.flush()
    await session.refresh(message)
    return MessageSchema.model_validate(message)
