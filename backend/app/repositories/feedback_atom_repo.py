"""Feedback atom repository — insert and query SLIFT-decomposed atoms."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scope_keys import expires_at_for_scope
from app.models.db.feedback_atom import FeedbackAtom


class FeedbackAtomSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_kind: str
    source_id: uuid.UUID
    mailbox: str
    atom_text: str
    role: str
    applies_when: str | None = None
    scope: str
    scope_key: str
    is_active: bool
    hit_count: int
    precision_num: int
    precision_den: int
    expires_at: datetime | None = None
    person_bound: bool
    promoted_from_atom_id: uuid.UUID | None = None
    created_at: datetime | None = None


async def insert_atom(
    session: AsyncSession,
    *,
    source_kind: str,
    source_id: uuid.UUID,
    mailbox: str,
    atom_text: str,
    atom_embedding: list[float],
    role: str,
    scope: str,
    scope_key: str,
    applies_when: str | None = None,
    person_bound: bool = False,
    promoted_from_atom_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
) -> FeedbackAtomSchema:
    atom = FeedbackAtom(
        source_kind=source_kind,
        source_id=source_id,
        mailbox=mailbox,
        atom_text=atom_text,
        atom_embedding=atom_embedding,
        role=role,
        applies_when=applies_when,
        scope=scope,
        scope_key=scope_key,
        person_bound=person_bound,
        promoted_from_atom_id=promoted_from_atom_id,
        expires_at=expires_at if expires_at is not None else expires_at_for_scope(scope),
    )
    session.add(atom)
    await session.flush()
    return FeedbackAtomSchema.model_validate(atom)


async def list_active_by_mailbox_scope(
    session: AsyncSession,
    *,
    mailbox: str,
    scope: str,
    scope_key: str,
    limit: int = 50,
) -> list[FeedbackAtomSchema]:
    stmt = (
        select(FeedbackAtom)
        .where(
            FeedbackAtom.mailbox == mailbox,
            FeedbackAtom.scope == scope,
            FeedbackAtom.scope_key == scope_key,
            FeedbackAtom.is_active.is_(True),
        )
        .order_by(FeedbackAtom.created_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    result = await session.execute(stmt)
    return [FeedbackAtomSchema.model_validate(row) for row in result.scalars().all()]


async def get_atom_by_id(
    session: AsyncSession,
    atom_id: uuid.UUID,
) -> FeedbackAtomSchema | None:
    stmt = select(FeedbackAtom).where(FeedbackAtom.id == atom_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return FeedbackAtomSchema.model_validate(row)


async def deactivate_atom(
    session: AsyncSession,
    atom_id: uuid.UUID,
) -> FeedbackAtomSchema | None:
    stmt = (
        sa_update(FeedbackAtom)
        .where(FeedbackAtom.id == atom_id)
        .values(is_active=False)
        .returning(FeedbackAtom)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return FeedbackAtomSchema.model_validate(row)


async def list_atoms(
    session: AsyncSession,
    *,
    mailbox: str | None = None,
    role: str | None = None,
    scope: str | None = None,
    is_active: bool | None = None,
    limit: int = 100,
) -> list[FeedbackAtomSchema]:
    stmt = (
        select(FeedbackAtom).order_by(FeedbackAtom.created_at.desc()).limit(max(1, min(limit, 500)))
    )
    if mailbox is not None:
        stmt = stmt.where(FeedbackAtom.mailbox == mailbox)
    if role is not None:
        stmt = stmt.where(FeedbackAtom.role == role)
    if scope is not None:
        stmt = stmt.where(FeedbackAtom.scope == scope)
    if is_active is not None:
        stmt = stmt.where(FeedbackAtom.is_active.is_(is_active))
    result = await session.execute(stmt)
    return [FeedbackAtomSchema.model_validate(row) for row in result.scalars().all()]
