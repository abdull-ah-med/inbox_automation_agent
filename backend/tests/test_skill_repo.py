"""Unit tests for skill_repo."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import SkillNameConflictError
from app.models.schemas.skill import SkillCreateSchema, SkillUpdateSchema
from app.repositories import skill_repo


def _skill_row(**overrides: object) -> MagicMock:
    row = MagicMock()
    row.id = overrides.get("id", uuid.uuid4())
    row.name = overrides.get("name", "Drug screen")
    row.description = overrides.get("description", "Handle screens")
    row.content = overrides.get("content", "Always CC Jordan")
    row.category = overrides.get("category", "escalation")
    row.always_apply = overrides.get("always_apply", False)
    row.is_active = overrides.get("is_active", True)
    row.source_kind = overrides.get("source_kind", "inline")
    row.imported_zip_sha256 = overrides.get("imported_zip_sha256")
    row.raw_frontmatter = overrides.get("raw_frontmatter")
    row.files = overrides.get("files", [])
    row.created_at = overrides.get("created_at", datetime.now(UTC))
    row.updated_at = overrides.get("updated_at", datetime.now(UTC))
    return row


@pytest.mark.asyncio
async def test_list_all() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [_skill_row(), _skill_row(name="Vendor")]
    session.execute = AsyncMock(return_value=result)

    rows = await skill_repo.list_all(session)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_list_active() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [_skill_row()]
    session.execute = AsyncMock(return_value=result)

    rows = await skill_repo.list_active(session)
    assert len(rows) == 1
    assert rows[0].is_active is True


@pytest.mark.asyncio
async def test_create_conflict() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    budget_result = MagicMock()
    budget_result.all.return_value = []
    session.execute = AsyncMock(return_value=budget_result)
    session.flush = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception()))

    with pytest.raises(SkillNameConflictError):
        await skill_repo.create(
            session,
            SkillCreateSchema(name="Dup", content="x", category="general"),
        )


@pytest.mark.asyncio
async def test_create_ok() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    budget_result = MagicMock()
    budget_result.all.return_value = []
    session.execute = AsyncMock(return_value=budget_result)
    session.flush = AsyncMock()

    async def fake_refresh(obj: object, **_kwargs: object) -> None:
        row = obj
        row.id = uuid.uuid4()  # type: ignore[attr-defined]
        row.created_at = datetime.now(UTC)  # type: ignore[attr-defined]
        row.updated_at = datetime.now(UTC)  # type: ignore[attr-defined]
        row.always_apply = False  # type: ignore[attr-defined]
        row.files = []  # type: ignore[attr-defined]
        row.source_kind = "inline"  # type: ignore[attr-defined]
        row.imported_zip_sha256 = None  # type: ignore[attr-defined]
        row.raw_frontmatter = None  # type: ignore[attr-defined]

    session.refresh = AsyncMock(side_effect=fake_refresh)

    created = await skill_repo.create(
        session,
        SkillCreateSchema(
            name="Tone",
            content="Be warm",
            description="d",
            category="general",
        ),
    )
    assert created.name == "Tone"
    assert created.content == "Be warm"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_active_budget_exceeded() -> None:
    from app.core.exceptions import SkillBudgetExceededError

    session = AsyncMock()
    budget_result = MagicMock()
    budget_result.all.return_value = [
        (uuid.uuid4(), "x" * 100) for _ in range(skill_repo.MAX_ACTIVE_SKILLS)
    ]
    session.execute = AsyncMock(return_value=budget_result)

    with pytest.raises(SkillBudgetExceededError):
        await skill_repo.create(
            session,
            SkillCreateSchema(name="Overflow", content="Too many", category="general"),
        )


@pytest.mark.asyncio
async def test_update_missing_returns_none() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)

    updated = await skill_repo.update_skill(
        session,
        uuid.uuid4(),
        SkillUpdateSchema(name="Nope"),
    )
    assert updated is None


@pytest.mark.asyncio
async def test_get_by_id() -> None:
    session = AsyncMock()
    row = _skill_row()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)

    found = await skill_repo.get_by_id(session, row.id)
    assert found is not None
    assert found.name == "Drug screen"


@pytest.mark.asyncio
async def test_update_ok() -> None:
    skill_id = uuid.uuid4()
    existing = _skill_row(id=skill_id)
    updated_row = _skill_row(id=skill_id, name="Renamed")
    get_result = MagicMock()
    get_result.scalar_one_or_none.return_value = existing
    budget_result = MagicMock()
    budget_result.all.return_value = []
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = updated_row
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[get_result, budget_result, update_result, update_result]
    )
    session.flush = AsyncMock()

    result = await skill_repo.update_skill(
        session,
        skill_id,
        SkillUpdateSchema(name="Renamed"),
    )
    assert result is not None
    assert result.name == "Renamed"


@pytest.mark.asyncio
async def test_update_empty_returns_existing() -> None:
    skill_id = uuid.uuid4()
    existing = _skill_row(id=skill_id)
    get_result = MagicMock()
    get_result.scalar_one_or_none.return_value = existing
    session = AsyncMock()
    session.execute = AsyncMock(return_value=get_result)

    result = await skill_repo.update_skill(session, skill_id, SkillUpdateSchema())
    assert result is not None
    assert result.name == existing.name


@pytest.mark.asyncio
async def test_update_conflict() -> None:
    skill_id = uuid.uuid4()
    existing = _skill_row(id=skill_id)
    get_result = MagicMock()
    get_result.scalar_one_or_none.return_value = existing
    budget_result = MagicMock()
    budget_result.all.return_value = []
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[get_result, budget_result, IntegrityError("stmt", {}, Exception())]
    )

    with pytest.raises(SkillNameConflictError):
        await skill_repo.update_skill(
            session,
            skill_id,
            SkillUpdateSchema(name="Taken"),
        )


@pytest.mark.asyncio
async def test_delete_ok() -> None:
    skill_id = uuid.uuid4()
    existing = _skill_row(id=skill_id)
    get_result = MagicMock()
    get_result.scalar_one_or_none.return_value = existing
    delete_files_result = MagicMock()
    delete_result = MagicMock()
    delete_result.rowcount = 1
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[get_result, delete_files_result, delete_result])
    session.flush = AsyncMock()

    assert await skill_repo.delete_skill(session, skill_id) is True
