"""Sent-reply repository — link outbound messages to threads/drafts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.sent_reply import SentReply

MatchedBy = Literal["approved_draft", "time_window", "manual"]


class SentReplySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thread_id: uuid.UUID
    message_id: uuid.UUID
    draft_id: uuid.UUID | None = None
    sent_body_snapshot: str
    sent_at: datetime
    matched_by: str
    created_at: datetime | None = None


async def insert_sent_reply(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    draft_id: uuid.UUID | None,
    sent_body_snapshot: str,
    sent_at: datetime,
    matched_by: MatchedBy,
) -> tuple[SentReplySchema, bool]:
    """Insert a sent_reply row. Returns ``(row, created)`` — idempotent on message_id."""
    insert_stmt = insert(SentReply).values(
        thread_id=thread_id,
        message_id=message_id,
        draft_id=draft_id,
        sent_body_snapshot=sent_body_snapshot,
        sent_at=sent_at,
        matched_by=matched_by,
    )
    upsert_stmt = insert_stmt.on_conflict_do_nothing(
        index_elements=["message_id"],
    ).returning(SentReply)
    result = await session.execute(upsert_stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        await session.flush()
        return SentReplySchema.model_validate(row), True

    existing = await get_by_message_id(session, message_id)
    if existing is None:
        raise RuntimeError(
            f"SentReply insert conflicted but row missing for message_id={message_id}"
        )
    return existing, False


async def get_by_message_id(
    session: AsyncSession,
    message_id: uuid.UUID,
) -> SentReplySchema | None:
    stmt = select(SentReply).where(SentReply.message_id == message_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return SentReplySchema.model_validate(row)


async def get_by_thread(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> SentReplySchema | None:
    """Return the most recent sent reply for a thread, if any."""
    stmt = (
        select(SentReply)
        .where(SentReply.thread_id == thread_id)
        .order_by(SentReply.sent_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return SentReplySchema.model_validate(row)


async def get_by_draft(
    session: AsyncSession,
    draft_id: uuid.UUID,
) -> SentReplySchema | None:
    stmt = select(SentReply).where(SentReply.draft_id == draft_id).limit(1)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return SentReplySchema.model_validate(row)


async def link_draft(
    session: AsyncSession,
    *,
    sent_reply_id: uuid.UUID,
    draft_id: uuid.UUID,
    matched_by: MatchedBy = "approved_draft",
) -> SentReplySchema | None:
    """Attach an approved draft to an existing sent_reply row."""
    from sqlalchemy import update

    stmt = (
        update(SentReply)
        .where(SentReply.id == sent_reply_id)
        .values(draft_id=draft_id, matched_by=matched_by)
        .returning(SentReply)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return SentReplySchema.model_validate(row)
