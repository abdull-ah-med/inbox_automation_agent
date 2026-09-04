"""Teaching note repository — CRUD for human-authored and promoted-atom notes."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scope_keys import expires_at_for_scope
from app.models.db.teaching_note import TeachingNote


class TeachingNoteSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mailbox: str
    title: str
    body: str
    applies_when: str | None = None
    scope: str
    scope_key: str
    status: str
    origin: str
    origin_atom_id: uuid.UUID | None = None
    person_bound: bool
    hit_count: int
    precision_num: int
    precision_den: int
    expires_at: datetime | None = None
    created_by_user_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


async def create_teaching_note(
    session: AsyncSession,
    *,
    mailbox: str,
    title: str,
    body: str,
    scope: str,
    scope_key: str,
    applies_when: str | None = None,
    origin: str = "manual",
    origin_atom_id: uuid.UUID | None = None,
    person_bound: bool = False,
    created_by_user_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
) -> TeachingNoteSchema:
    note = TeachingNote(
        mailbox=mailbox,
        title=title,
        body=body,
        applies_when=applies_when,
        scope=scope,
        scope_key=scope_key,
        origin=origin,
        origin_atom_id=origin_atom_id,
        person_bound=person_bound,
        created_by_user_id=created_by_user_id,
        expires_at=expires_at if expires_at is not None else expires_at_for_scope(scope),
    )
    session.add(note)
    await session.flush()
    return TeachingNoteSchema.model_validate(note)


async def list_teaching_notes(
    session: AsyncSession,
    *,
    mailbox: str | None = None,
    scope: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[TeachingNoteSchema]:
    stmt = (
        select(TeachingNote).order_by(TeachingNote.created_at.desc()).limit(max(1, min(limit, 500)))
    )
    if mailbox is not None:
        stmt = stmt.where(TeachingNote.mailbox == mailbox)
    if status is not None:
        stmt = stmt.where(TeachingNote.status == status)
    if scope is not None:
        stmt = stmt.where(TeachingNote.scope == scope)
    result = await session.execute(stmt)
    return [TeachingNoteSchema.model_validate(row) for row in result.scalars().all()]


async def get_teaching_note_by_id(
    session: AsyncSession,
    note_id: uuid.UUID,
) -> TeachingNoteSchema | None:
    stmt = select(TeachingNote).where(TeachingNote.id == note_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return TeachingNoteSchema.model_validate(row)


async def update_teaching_note_status(
    session: AsyncSession,
    note_id: uuid.UUID,
    status: str,
) -> TeachingNoteSchema | None:
    stmt = (
        sa_update(TeachingNote)
        .where(TeachingNote.id == note_id)
        .values(status=status)
        .returning(TeachingNote)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return TeachingNoteSchema.model_validate(row)


async def update_teaching_note(
    session: AsyncSession,
    note_id: uuid.UUID,
    *,
    body: str | None = None,
    applies_when: str | None = None,
    title: str | None = None,
) -> TeachingNoteSchema | None:
    """Update body / applies_when / title without touching scope or status."""
    updates: dict[str, object] = {}
    if body is not None:
        updates["body"] = body
    if applies_when is not None:
        updates["applies_when"] = applies_when
    if title is not None:
        updates["title"] = title
    if not updates:
        return await get_teaching_note_by_id(session, note_id)
    stmt = (
        sa_update(TeachingNote)
        .where(TeachingNote.id == note_id)
        .values(**updates)
        .returning(TeachingNote)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return TeachingNoteSchema.model_validate(row)


async def update_teaching_note_scope(
    session: AsyncSession,
    note_id: uuid.UUID,
    *,
    scope: str,
    scope_key: str,
    expires_at: datetime | None,
) -> TeachingNoteSchema | None:
    stmt = (
        sa_update(TeachingNote)
        .where(TeachingNote.id == note_id)
        .values(scope=scope, scope_key=scope_key, expires_at=expires_at)
        .returning(TeachingNote)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return TeachingNoteSchema.model_validate(row)


async def increment_hit_count(session: AsyncSession, note_id: uuid.UUID) -> None:
    """Increment hit_count for a retrieved teaching note."""
    stmt = (
        sa_update(TeachingNote)
        .where(TeachingNote.id == note_id)
        .values(hit_count=TeachingNote.hit_count + 1)
    )
    await session.execute(stmt)
