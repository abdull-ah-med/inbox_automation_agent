"""Persist thread-level summaries (one row per thread)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.thread_summary import ThreadSummary


class ThreadSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    thread_id: uuid.UUID
    summary_text: str
    message_count: int
    last_message_id: uuid.UUID
    generated_at: datetime | None = None


async def get(session: AsyncSession, thread_id: uuid.UUID) -> ThreadSummarySchema | None:
    row = (
        await session.execute(select(ThreadSummary).where(ThreadSummary.thread_id == thread_id))
    ).scalar_one_or_none()
    if row is None:
        return None
    return ThreadSummarySchema.model_validate(row)


async def upsert(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    summary_text: str,
    message_count: int,
    last_message_id: uuid.UUID,
) -> ThreadSummarySchema:
    stmt = (
        pg_insert(ThreadSummary)
        .values(
            thread_id=thread_id,
            summary_text=summary_text,
            message_count=message_count,
            last_message_id=last_message_id,
        )
        .on_conflict_do_update(
            index_elements=["thread_id"],
            set_={
                "summary_text": summary_text,
                "message_count": message_count,
                "last_message_id": last_message_id,
                "generated_at": stmt_now(),
            },
        )
        .returning(ThreadSummary)
    )
    row = (await session.execute(stmt)).scalar_one()
    return ThreadSummarySchema.model_validate(row)


def stmt_now() -> datetime:
    from datetime import UTC, datetime as dt

    return dt.now(UTC)
