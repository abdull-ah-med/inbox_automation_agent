"""Rejection memory repository — store rejects, retrieve as negative constraints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.db.rejection_memory import RejectionMemory
from app.repositories._vector_common import set_hnsw_session_defaults
from app.utils.text import truncate_display

_NOTE_DISPLAY_MAX = 240


class RejectionMemorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    mailbox: str
    routing_category: str
    reason_code: str
    note: str
    is_excluded: bool = False
    created_at: datetime | None = None


def format_constraint_line(*, reason_code: str, note: str) -> str:
    text = truncate_display(note.strip(), _NOTE_DISPLAY_MAX)
    return f"[{reason_code}] {text}"


async def store_rejection_memory(
    session: AsyncSession,
    *,
    draft_id: uuid.UUID,
    mailbox: str,
    routing_category: str,
    reason_code: str,
    note: str,
    embedding: list[float],
) -> RejectionMemorySchema:
    existing_stmt = select(RejectionMemory).where(RejectionMemory.draft_id == draft_id)
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return RejectionMemorySchema.model_validate(existing)

    stmt = (
        pg_insert(RejectionMemory)
        .values(
            draft_id=draft_id,
            mailbox=mailbox,
            routing_category=routing_category,
            reason_code=reason_code,
            note=note,
            embedding=embedding,
        )
        .on_conflict_do_nothing(index_elements=["draft_id"])
        .returning(RejectionMemory)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        await session.flush()
        return RejectionMemorySchema.model_validate(row)

    existing = (
        await session.execute(select(RejectionMemory).where(RejectionMemory.draft_id == draft_id))
    ).scalar_one()
    return RejectionMemorySchema.model_validate(existing)


async def count_for_bucket(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    reason_code: str,
) -> int:
    stmt = (
        select(func.count())
        .select_from(RejectionMemory)
        .where(
            RejectionMemory.mailbox == mailbox,
            RejectionMemory.routing_category == routing_category,
            RejectionMemory.reason_code == reason_code,
            RejectionMemory.is_excluded.is_(False),
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def list_for_bucket(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    reason_code: str | None = None,
    limit: int = 20,
) -> list[RejectionMemorySchema]:
    stmt = (
        select(RejectionMemory)
        .where(
            RejectionMemory.mailbox == mailbox,
            RejectionMemory.routing_category == routing_category,
            RejectionMemory.is_excluded.is_(False),
        )
        .order_by(RejectionMemory.created_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    if reason_code is not None:
        stmt = stmt.where(RejectionMemory.reason_code == reason_code)
    result = await session.execute(stmt)
    return [RejectionMemorySchema.model_validate(row) for row in result.scalars().all()]


async def list_notes_for_bucket(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    reason_code: str,
    limit: int = 20,
) -> list[str]:
    rows = await list_for_bucket(
        session,
        mailbox=mailbox,
        routing_category=routing_category,
        reason_code=reason_code,
        limit=limit,
    )
    return [row.note for row in rows]


async def list_ids_for_bucket(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    reason_code: str,
    limit: int = 20,
) -> list[uuid.UUID]:
    rows = await list_for_bucket(
        session,
        mailbox=mailbox,
        routing_category=routing_category,
        reason_code=reason_code,
        limit=limit,
    )
    return [row.id for row in rows]


async def list_recent_for_category(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    limit: int = 3,
) -> list[RejectionMemorySchema]:
    return await list_for_bucket(
        session,
        mailbox=mailbox,
        routing_category=routing_category,
        limit=limit,
    )


async def find_similar_constraints(
    session: AsyncSession,
    *,
    query_embedding: list[float],
    mailbox: str,
    routing_category: str,
    limit: int = 3,
) -> list[str]:
    if limit < 1:
        return []
    await set_hnsw_session_defaults(session, get_settings())
    distance = RejectionMemory.embedding.cosine_distance(query_embedding)
    stmt = (
        select(
            RejectionMemory.reason_code,
            RejectionMemory.note,
            distance.label("distance"),
        )
        .where(
            RejectionMemory.is_excluded.is_(False),
            RejectionMemory.mailbox == mailbox,
            RejectionMemory.routing_category == routing_category,
        )
        .order_by(distance)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        format_constraint_line(reason_code=row.reason_code, note=row.note)
        for row in result.all()
        if row.note
    ]


async def set_excluded_for_ids(
    session: AsyncSession,
    memory_ids: list[uuid.UUID],
    *,
    is_excluded: bool = True,
) -> int:
    if not memory_ids:
        return 0
    stmt = (
        sa_update(RejectionMemory)
        .where(RejectionMemory.id.in_(memory_ids))
        .values(is_excluded=is_excluded)
    )
    result = await session.execute(stmt)
    await session.flush()
    return int(getattr(result, "rowcount", 0) or 0)
