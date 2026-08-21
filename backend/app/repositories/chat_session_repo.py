"""Chat session repository — JSONB message history per user."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.chat_session import ChatSession


class ChatSessionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    mailbox: str | None = None
    messages: list = Field(default_factory=list)
    summary: str = ""
    created_at: datetime
    last_message_at: datetime | None = None


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    mailbox: str | None,
) -> ChatSessionSchema:
    row = ChatSession(
        user_id=user_id,
        mailbox=mailbox,
        messages=[],
        summary="",
    )
    session.add(row)
    await session.flush()
    return ChatSessionSchema.model_validate(row)


async def get_for_user(
    session: AsyncSession,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ChatSessionSchema | None:
    stmt = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == user_id,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return ChatSessionSchema.model_validate(row)


async def update_messages(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    messages: list,
    summary: str,
    last_message_at: datetime,
) -> ChatSessionSchema | None:
    stmt = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == user_id,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    row.messages = messages
    row.summary = summary
    row.last_message_at = last_message_at
    await session.flush()
    return ChatSessionSchema.model_validate(row)
