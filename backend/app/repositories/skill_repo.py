"""Skill repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import SkillBudgetExceededError, SkillNameConflictError
from app.models.db.skill import Skill
from app.models.schemas.skill import (
    SkillCreateSchema,
    SkillResponseSchema,
    SkillUpdateSchema,
)

# Caps how many standing instructions can be injected into every draft LLM call.
MAX_ACTIVE_SKILLS = 20
MAX_ACTIVE_SKILLS_CHARS = 50_000


class SkillSelectionRow(BaseModel):
    """Internal skill row including optional embedding for routing."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    content: str
    category: str | None = None
    always_apply: bool = False
    is_active: bool = True
    embedding: list[float] | None = None


def _validate_category_rules(
    *,
    always_apply: bool,
    category: str | None,
) -> None:
    if not always_apply and category is None:
        raise ValueError("category is required when always_apply is false")


def _to_response(row: Skill) -> SkillResponseSchema:
    return SkillResponseSchema.model_validate(row)


def _to_selection(row: Skill) -> SkillSelectionRow:
    return SkillSelectionRow.model_validate(row)


async def list_all(session: AsyncSession) -> list[SkillResponseSchema]:
    stmt = select(Skill).order_by(Skill.created_at.desc())
    result = await session.execute(stmt)
    return [_to_response(row) for row in result.scalars().all()]


async def list_active(session: AsyncSession) -> list[SkillResponseSchema]:
    stmt = select(Skill).where(Skill.is_active.is_(True)).order_by(Skill.name.asc())
    result = await session.execute(stmt)
    return [_to_response(row) for row in result.scalars().all()]


async def list_active_for_selection(session: AsyncSession) -> list[SkillSelectionRow]:
    """Active skills including embeddings for the routing funnel."""
    stmt = select(Skill).where(Skill.is_active.is_(True)).order_by(Skill.name.asc())
    result = await session.execute(stmt)
    return [_to_selection(row) for row in result.scalars().all()]


async def get_by_id(
    session: AsyncSession,
    skill_id: uuid.UUID,
) -> SkillResponseSchema | None:
    stmt = select(Skill).where(Skill.id == skill_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_response(row)


async def _active_budget(
    session: AsyncSession,
    *,
    exclude_id: uuid.UUID | None = None,
) -> tuple[int, int]:
    """Return (active_count, total_content_chars) excluding optional skill id."""
    stmt = select(Skill.id, Skill.content).where(Skill.is_active.is_(True))
    if exclude_id is not None:
        stmt = stmt.where(Skill.id != exclude_id)
    result = await session.execute(stmt)
    rows = result.all()
    return len(rows), sum(len(content or "") for _, content in rows)


async def _assert_active_budget(
    session: AsyncSession,
    *,
    content: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    count, chars = await _active_budget(session, exclude_id=exclude_id)
    if count + 1 > MAX_ACTIVE_SKILLS:
        raise SkillBudgetExceededError(f"Active skill limit exceeded (max {MAX_ACTIVE_SKILLS})")
    if chars + len(content) > MAX_ACTIVE_SKILLS_CHARS:
        raise SkillBudgetExceededError(
            f"Active skill character budget exceeded (max {MAX_ACTIVE_SKILLS_CHARS})"
        )


async def create(
    session: AsyncSession,
    data: SkillCreateSchema,
) -> SkillResponseSchema:
    _validate_category_rules(always_apply=data.always_apply, category=data.category)
    if data.is_active:
        await _assert_active_budget(session, content=data.content)

    row = Skill(
        name=data.name,
        description=data.description,
        content=data.content,
        category=data.category,
        always_apply=data.always_apply,
        is_active=data.is_active,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise SkillNameConflictError(f"Skill name already exists: {data.name}") from exc
    await session.refresh(row)
    return _to_response(row)


async def update_skill(
    session: AsyncSession,
    skill_id: uuid.UUID,
    data: SkillUpdateSchema,
) -> SkillResponseSchema | None:
    existing = await get_by_id(session, skill_id)
    if existing is None:
        return None

    values = data.model_dump(exclude_unset=True)
    if not values:
        return existing

    will_be_always = values.get("always_apply", existing.always_apply)
    next_category = values.get("category", existing.category)
    if "category" in values and values["category"] is None and not will_be_always:
        next_category = None
    _validate_category_rules(always_apply=will_be_always, category=next_category)

    will_be_active = values.get("is_active", existing.is_active)
    next_content = values.get("content", existing.content)
    if will_be_active:
        await _assert_active_budget(
            session,
            content=next_content,
            exclude_id=skill_id,
        )

    values["updated_at"] = datetime.now(UTC)

    stmt = sa_update(Skill).where(Skill.id == skill_id).values(**values).returning(Skill)
    try:
        result = await session.execute(stmt)
    except IntegrityError as exc:
        raise SkillNameConflictError(f"Skill name already exists: {values.get('name')}") from exc
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return _to_response(row)


async def update_embedding(
    session: AsyncSession,
    skill_id: uuid.UUID,
    *,
    embedding: list[float],
) -> None:
    stmt = (
        sa_update(Skill)
        .where(Skill.id == skill_id)
        .values(embedding=embedding, updated_at=datetime.now(UTC))
    )
    await session.execute(stmt)
    await session.flush()


async def rank_by_cosine(
    session: AsyncSession,
    *,
    query_embedding: list[float],
    skill_ids: list[uuid.UUID],
    limit: int = 8,
) -> list[SkillSelectionRow]:
    """Return skills ordered by cosine distance (closest first)."""
    if not skill_ids or limit < 1:
        return []
    distance = Skill.embedding.cosine_distance(query_embedding)
    stmt = (
        select(Skill)
        .where(
            Skill.id.in_(skill_ids),
            Skill.embedding.is_not(None),
        )
        .order_by(distance)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [_to_selection(row) for row in result.scalars().all()]


async def delete_skill(session: AsyncSession, skill_id: uuid.UUID) -> bool:
    existing = await get_by_id(session, skill_id)
    if existing is None:
        return False
    stmt = sa_delete(Skill).where(Skill.id == skill_id)
    result = await session.execute(stmt)
    await session.flush()
    return (result.rowcount or 0) > 0  # type: ignore[attr-defined]
