"""Persist sibling/associated confirm and dismiss decisions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.thread_association_review import ThreadAssociationReview


async def confirmed_pairs(
    session: AsyncSession,
    source_thread_id: uuid.UUID,
) -> list[tuple[uuid.UUID, float | None]]:
    stmt = select(
        ThreadAssociationReview.related_thread_id,
        ThreadAssociationReview.score,
    ).where(
        ThreadAssociationReview.source_thread_id == source_thread_id,
        ThreadAssociationReview.status == "confirmed",
    )
    result = await session.execute(stmt)
    return [(row.related_thread_id, row.score) for row in result.all()]


async def statuses_for_source(
    session: AsyncSession,
    source_thread_id: uuid.UUID,
) -> dict[uuid.UUID, str]:
    stmt = select(
        ThreadAssociationReview.related_thread_id,
        ThreadAssociationReview.status,
    ).where(ThreadAssociationReview.source_thread_id == source_thread_id)
    result = await session.execute(stmt)
    return {row.related_thread_id: row.status for row in result.all()}


async def upsert_proposed(
    session: AsyncSession,
    *,
    source_thread_id: uuid.UUID,
    related_thread_id: uuid.UUID,
    score: float,
) -> None:
    stmt = (
        insert(ThreadAssociationReview)
        .values(
            source_thread_id=source_thread_id,
            related_thread_id=related_thread_id,
            status="proposed",
            score=score,
        )
        .on_conflict_do_nothing(
            constraint="uq_thread_association_reviews_pair",
        )
    )
    await session.execute(stmt)


async def set_status(
    session: AsyncSession,
    *,
    source_thread_id: uuid.UUID,
    related_thread_id: uuid.UUID,
    status: str,
    actor: str,
    score: float | None = None,
) -> str:
    now = datetime.now(UTC)
    existing = await session.execute(
        select(ThreadAssociationReview).where(
            ThreadAssociationReview.source_thread_id == source_thread_id,
            ThreadAssociationReview.related_thread_id == related_thread_id,
        )
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        await session.execute(
            update(ThreadAssociationReview)
            .where(ThreadAssociationReview.id == row.id)
            .values(status=status, actor=actor, decided_at=now)
        )
        return status

    await session.execute(
        insert(ThreadAssociationReview).values(
            source_thread_id=source_thread_id,
            related_thread_id=related_thread_id,
            status=status,
            score=score,
            actor=actor,
            decided_at=now,
        )
    )
    return status
