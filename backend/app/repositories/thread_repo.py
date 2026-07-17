"""Thread repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
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
    *,
    mailbox: str,
) -> ThreadSchema | None:
    """Look up a thread by mailbox + conversation_id (unique per mailbox)."""
    stmt = select(Thread).where(
        Thread.mailbox == mailbox,
        Thread.conversation_id == conversation_id,
    )
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
    """Insert a thread or update subject/last_message_at for mailbox+conversation_id.

    Uses Postgres ``ON CONFLICT`` so concurrent webhook+poll writers do not race.
    """
    insert_stmt = insert(Thread).values(
        mailbox=mailbox,
        conversation_id=conversation_id,
        subject=subject,
        state=state,
        last_message_at=last_message_at,
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_threads_mailbox_conversation",
        set_={
            "subject": subject,
            "last_message_at": last_message_at,
        },
    ).returning(Thread)
    result = await session.execute(upsert_stmt)
    thread = result.scalar_one()
    await session.flush()
    return ThreadSchema.model_validate(thread)
