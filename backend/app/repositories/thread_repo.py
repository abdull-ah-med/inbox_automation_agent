"""Thread repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum


class ThreadSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mailbox: str
    conversation_id: str
    subject: str
    state: str
    urgency: str | None = None
    category: str | None = None
    last_message_at: datetime | None = None
    last_updated_at: datetime


async def get_by_conversation_id(
    session: AsyncSession,
    conversation_id: str,
) -> ThreadSchema | None:
    stmt = select(Thread).where(Thread.conversation_id == conversation_id)
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()
    if thread is None:
        return None
    return ThreadSchema.model_validate(thread)


async def get_by_id(session: AsyncSession, thread_id: uuid.UUID) -> ThreadSchema | None:
    stmt = select(Thread).where(Thread.id == thread_id)
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()
    if thread is None:
        return None
    return ThreadSchema.model_validate(thread)


async def upsert_thread(
    session: AsyncSession,
    *,
    mailbox: str,
    conversation_id: str,
    subject: str,
    last_message_at: datetime | None = None,
    state: str = ThreadStateEnum.NEW.value,
) -> ThreadSchema:
    """Insert a thread or update subject/last_message_at if conversation_id exists."""
    stmt = select(Thread).where(Thread.conversation_id == conversation_id)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.subject = subject
        if last_message_at is not None:
            existing.last_message_at = last_message_at
        await session.flush()
        await session.refresh(existing)
        return ThreadSchema.model_validate(existing)

    thread = Thread(
        mailbox=mailbox,
        conversation_id=conversation_id,
        subject=subject,
        state=state,
        last_message_at=last_message_at,
    )
    session.add(thread)
    await session.flush()
    await session.refresh(thread)
    return ThreadSchema.model_validate(thread)
