"""Persist per-thread pins and ADD-only fact log."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.core.exceptions import ThreadContextVersionConflict
from app.models.db.thread_context import ThreadContext, ThreadContextFact


class ThreadContextRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    thread_id: uuid.UUID
    user_notes: str
    version: int
    extract_input_hash: str
    last_message_id_at_extract: uuid.UUID | None = None
    updated_at: datetime | None = None
    created_at: datetime | None = None
    extract_status: str = "idle"
    extract_started_at: datetime | None = None
    last_extract_error: str | None = None


class FactRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thread_id: uuid.UUID
    body: str
    source_message_id: uuid.UUID | None = None
    actor_kind: str
    superseded_at: datetime | None = None
    superseded_by: uuid.UUID | None = None
    created_at: datetime | None = None


async def get(session: AsyncSession, thread_id: uuid.UUID) -> ThreadContextRow | None:
    row = (
        await session.execute(select(ThreadContext).where(ThreadContext.thread_id == thread_id))
    ).scalar_one_or_none()
    if row is None:
        return None
    return ThreadContextRow.model_validate(row)


async def get_or_create(session: AsyncSession, thread_id: uuid.UUID) -> ThreadContextRow:
    stmt = (
        pg_insert(ThreadContext)
        .values(thread_id=thread_id)
        .on_conflict_do_nothing(index_elements=["thread_id"])
    )
    await session.execute(stmt)
    row = await get(session, thread_id)
    if row is None:
        raise RuntimeError("thread_context get_or_create failed to persist pointer")
    return row


async def save_user_notes(
    session: AsyncSession,
    thread_id: uuid.UUID,
    notes: str,
    expected_version: int,
) -> ThreadContextRow:
    stmt = (
        sa_update(ThreadContext)
        .where(
            ThreadContext.thread_id == thread_id,
            ThreadContext.version == expected_version,
        )
        .values(
            user_notes=notes,
            version=ThreadContext.version + 1,
            updated_at=func.now(),
        )
        .returning(ThreadContext)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ThreadContextVersionConflict()
    return ThreadContextRow.model_validate(row)


async def list_active_facts(session: AsyncSession, thread_id: uuid.UUID) -> list[FactRow]:
    stmt = (
        select(ThreadContextFact)
        .where(
            ThreadContextFact.thread_id == thread_id,
            ThreadContextFact.superseded_at.is_(None),
        )
        .order_by(ThreadContextFact.created_at.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [FactRow.model_validate(row) for row in rows]


async def add_facts(
    session: AsyncSession,
    thread_id: uuid.UUID,
    facts: list[dict[str, Any]],
) -> list[FactRow]:
    created: list[ThreadContextFact] = []
    for fact in facts:
        row = ThreadContextFact(
            thread_id=thread_id,
            body=fact["body"],
            source_message_id=fact.get("source_message_id"),
            actor_kind=fact["actor_kind"],
        )
        session.add(row)
        created.append(row)
    await session.flush()
    return [FactRow.model_validate(row) for row in created]


async def supersede_facts(
    session: AsyncSession,
    ids: list[uuid.UUID],
    superseded_by: uuid.UUID | None,
) -> None:
    if not ids:
        return
    stmt = (
        sa_update(ThreadContextFact)
        .where(ThreadContextFact.id.in_(ids))
        .values(superseded_at=func.now(), superseded_by=superseded_by)
    )
    await session.execute(stmt)


async def discard_facts(session: AsyncSession, ids: list[uuid.UUID]) -> None:
    await supersede_facts(session, ids, superseded_by=None)


async def set_extract_hash(
    session: AsyncSession,
    thread_id: uuid.UUID,
    extract_hash: str,
    last_message_id: uuid.UUID | None,
) -> None:
    stmt = (
        sa_update(ThreadContext)
        .where(ThreadContext.thread_id == thread_id)
        .values(
            extract_input_hash=extract_hash,
            last_message_id_at_extract=last_message_id,
            updated_at=func.now(),
        )
    )
    await session.execute(stmt)


async def set_extract_status(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    status: str,
    started_at: datetime | None = None,
    error: str | None = None,
) -> None:
    values: dict[str, Any] = {
        "extract_status": status,
        "extract_started_at": started_at,
        "last_extract_error": error,
        "updated_at": func.now(),
    }
    stmt = sa_update(ThreadContext).where(ThreadContext.thread_id == thread_id).values(**values)
    await session.execute(stmt)


async def cas_begin_rebuild(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    now: datetime,
    stale_after: timedelta,
) -> bool:
    """Mark extract running iff idle/failed or a stale running row. Returns True on win."""
    stale_before = now - stale_after
    stmt = (
        sa_update(ThreadContext)
        .where(
            ThreadContext.thread_id == thread_id,
            or_(
                ThreadContext.extract_status != "running",
                ThreadContext.extract_started_at.is_(None),
                ThreadContext.extract_started_at < stale_before,
            ),
        )
        .values(
            extract_status="running",
            extract_started_at=now,
            last_extract_error=None,
            updated_at=func.now(),
        )
        .returning(ThreadContext.thread_id)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row is not None
