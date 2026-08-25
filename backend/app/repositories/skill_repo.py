"""Skill repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, NoReturn

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import (
    SkillBudgetExceededError,
    SkillNameConflictError,
    SkillNotFoundError,
)
from app.models.db.skill import Skill
from app.models.db.skill_file import SkillFile
from app.models.schemas.skill import (
    SkillCreateSchema,
    SkillResponseSchema,
    SkillSourceKind,
    SkillUpdateSchema,
)
from app.repositories import skill_files_repo
from app.repositories._vector_common import set_hnsw_session_defaults


def _raise_name_conflict(exc: IntegrityError, name: object) -> NoReturn:
    raise SkillNameConflictError(f"Skill name already exists: {name}") from exc

# Caps how many standing instructions can be injected into every draft LLM call.
MAX_ACTIVE_SKILLS = 20
MAX_ACTIVE_SKILLS_CHARS = 200_000


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
    reference_manifest: list[str] = Field(default_factory=list)
    has_assets: bool = False


def _validate_category_rules(
    *,
    always_apply: bool,
    category: str | None,
) -> None:
    if not always_apply and category is None:
        raise ValueError("category is required when always_apply is false")


def _file_counts(row: Skill) -> tuple[int, int]:
    files = getattr(row, "files", None) or []
    refs = sum(1 for f in files if f.kind == "reference")
    assets = sum(1 for f in files if f.kind == "asset")
    return refs, assets


def _to_response(row: Skill) -> SkillResponseSchema:
    refs, assets = _file_counts(row)
    source_kind: SkillSourceKind = "imported" if row.source_kind == "imported" else "inline"
    return SkillResponseSchema(
        id=row.id,
        name=row.name,
        description=row.description,
        content=row.content,
        category=row.category,
        always_apply=row.always_apply,
        is_active=row.is_active,
        source_kind=source_kind,
        imported_zip_sha256=row.imported_zip_sha256,
        raw_frontmatter=row.raw_frontmatter,
        reference_file_count=refs,
        asset_file_count=assets,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_selection(row: Skill) -> SkillSelectionRow:
    files = getattr(row, "files", None) or []
    refs = sorted(f.relative_path for f in files if f.kind == "reference")
    has_assets = any(f.kind == "asset" for f in files)
    return SkillSelectionRow(
        id=row.id,
        name=row.name,
        description=row.description,
        content=row.content,
        category=row.category,
        always_apply=row.always_apply,
        is_active=row.is_active,
        embedding=row.embedding,
        reference_manifest=refs,
        has_assets=has_assets,
    )


async def list_all(session: AsyncSession) -> list[SkillResponseSchema]:
    stmt = select(Skill).options(selectinload(Skill.files)).order_by(Skill.created_at.desc())
    result = await session.execute(stmt)
    return [_to_response(row) for row in result.scalars().all()]


async def list_active(session: AsyncSession) -> list[SkillResponseSchema]:
    stmt = (
        select(Skill)
        .options(selectinload(Skill.files))
        .where(Skill.is_active.is_(True))
        .order_by(Skill.name.asc())
    )
    result = await session.execute(stmt)
    return [_to_response(row) for row in result.scalars().all()]


async def list_active_for_selection(session: AsyncSession) -> list[SkillSelectionRow]:
    """Active skills including embeddings and reference manifests for routing."""
    stmt = (
        select(Skill)
        .options(selectinload(Skill.files))
        .where(Skill.is_active.is_(True))
        .order_by(Skill.name.asc())
    )
    result = await session.execute(stmt)
    return [_to_selection(row) for row in result.scalars().all()]


async def get_by_id(
    session: AsyncSession,
    skill_id: uuid.UUID,
) -> SkillResponseSchema | None:
    stmt = select(Skill).options(selectinload(Skill.files)).where(Skill.id == skill_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_response(row)


async def get_by_name(session: AsyncSession, name: str) -> SkillResponseSchema | None:
    stmt = select(Skill).options(selectinload(Skill.files)).where(Skill.name == name)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_response(row)


async def get_by_import_hash(
    session: AsyncSession,
    sha256: str,
) -> SkillResponseSchema | None:
    stmt = (
        select(Skill).options(selectinload(Skill.files)).where(Skill.imported_zip_sha256 == sha256)
    )
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
    *,
    source_kind: SkillSourceKind = "inline",
    imported_zip_sha256: str | None = None,
    raw_frontmatter: dict[str, Any] | None = None,
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
        source_kind=source_kind,
        imported_zip_sha256=imported_zip_sha256,
        raw_frontmatter=raw_frontmatter,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        _raise_name_conflict(exc, data.name)
    await session.refresh(row, attribute_names=["files"])
    return _to_response(row)


async def upsert_imported(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    content: str,
    category: str | None,
    always_apply: bool,
    imported_zip_sha256: str,
    raw_frontmatter: dict[str, Any] | None,
    files: list[dict[str, object]],
    overwrite: bool,
    target_skill_id: uuid.UUID | None = None,
) -> tuple[SkillResponseSchema, bool]:
    """Create or overwrite an imported skill + bundled files.

    Returns (skill, overwritten). Writes ORM directly so SKILL.md bodies may
    exceed the inline SkillCreateSchema max_length (10k).

    When ``target_skill_id`` is set, that row is overwritten (name may change).
    Otherwise lookup is by ``name``.
    """
    _validate_category_rules(always_apply=always_apply, category=category)
    existing: SkillResponseSchema | None = None
    if target_skill_id is not None:
        existing = await get_by_id(session, target_skill_id)
        if existing is None:
            raise SkillNotFoundError(f"Skill not found for overwrite: {target_skill_id}")
    else:
        existing = await get_by_name(session, name)
        if existing is not None and not overwrite:
            raise SkillNameConflictError(f"Skill name already exists: {name}")

    if existing is None:
        await _assert_active_budget(session, content=content)
        row = Skill(
            name=name,
            description=description,
            content=content,
            category=category,
            always_apply=always_apply,
            is_active=True,
            source_kind="imported",
            imported_zip_sha256=imported_zip_sha256,
            raw_frontmatter=raw_frontmatter,
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError as exc:
            _raise_name_conflict(exc, name)
        await skill_files_repo.replace_files(session, skill_id=row.id, files=files)
        refreshed = await get_by_id(session, row.id)
        assert refreshed is not None
        return refreshed, False

    await _assert_active_budget(session, content=content, exclude_id=existing.id)
    values: dict[str, Any] = {
        "name": name,
        "description": description,
        "content": content,
        "category": category,
        "always_apply": always_apply,
        "is_active": True,
        "source_kind": "imported",
        "imported_zip_sha256": imported_zip_sha256,
        "raw_frontmatter": raw_frontmatter,
        "updated_at": datetime.now(UTC),
    }
    stmt = sa_update(Skill).where(Skill.id == existing.id).values(**values).returning(Skill)
    try:
        result = await session.execute(stmt)
    except IntegrityError as exc:
        _raise_name_conflict(exc, name)
    row = result.scalar_one()
    await session.flush()
    await skill_files_repo.replace_files(session, skill_id=row.id, files=files)
    refreshed = await get_by_id(session, row.id)
    assert refreshed is not None
    return refreshed, True


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
        _raise_name_conflict(exc, values.get("name"))
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return await get_by_id(session, skill_id)


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


class SkillSimilarityHit(BaseModel):
    """Cosine-similarity match for duplicate-skill detection on import."""

    id: uuid.UUID
    name: str
    similarity: float


async def find_similar(
    session: AsyncSession,
    *,
    embedding: list[float],
    threshold: float,
    limit: int = 3,
) -> list[SkillSimilarityHit]:
    """Return active skills at or above ``threshold`` cosine similarity.

    ``threshold`` maps to cosine distance ``1 - similarity``.
    """
    if limit < 1:
        return []
    await set_hnsw_session_defaults(session, get_settings())
    max_distance = 1.0 - threshold
    distance = Skill.embedding.cosine_distance(embedding)
    similarity = (1 - distance).label("similarity")
    stmt = (
        select(Skill.id, Skill.name, similarity)
        .where(
            Skill.is_active.is_(True),
            Skill.embedding.is_not(None),
            distance <= max_distance,
        )
        .order_by(distance)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        SkillSimilarityHit(
            id=row.id,
            name=row.name,
            similarity=float(row.similarity),
        )
        for row in result.all()
    ]


async def delete_skill(session: AsyncSession, skill_id: uuid.UUID) -> bool:
    existing = await get_by_id(session, skill_id)
    if existing is None:
        return False
    # Explicit file delete first keeps behavior clear even without ORM cascade flush.
    await session.execute(sa_delete(SkillFile).where(SkillFile.skill_id == skill_id))
    stmt = sa_delete(Skill).where(Skill.id == skill_id)
    result = await session.execute(stmt)
    await session.flush()
    return (result.rowcount or 0) > 0  # type: ignore[attr-defined]
