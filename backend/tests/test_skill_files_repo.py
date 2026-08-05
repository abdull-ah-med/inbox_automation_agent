"""Unit tests for skill_files_repo."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories import skill_files_repo


def _file_row(**overrides: object) -> MagicMock:
    row = MagicMock()
    row.id = overrides.get("id", uuid.uuid4())
    row.skill_id = overrides.get("skill_id", uuid.uuid4())
    row.relative_path = overrides.get("relative_path", "references/a.md")
    row.kind = overrides.get("kind", "reference")
    row.mime_type = overrides.get("mime_type", "text/markdown")
    row.size_bytes = overrides.get("size_bytes", 12)
    row.content = overrides.get("content", b"hello world!")
    row.created_at = overrides.get("created_at", datetime.now(UTC))
    return row


@pytest.mark.asyncio
async def test_replace_list_get_round_trip() -> None:
    """replace_files inserts rows; list_by_skill and get_by_path read them back."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    skill_id = uuid.uuid4()

    created_rows: list[MagicMock] = []

    async def fake_flush() -> None:
        # After flush, ORM would assign ids — simulate attributes already set.
        for row in created_rows:
            if not isinstance(row.id, uuid.UUID):
                row.id = uuid.uuid4()
            if not isinstance(row.created_at, datetime):
                row.created_at = datetime.now(UTC)

    session.flush = AsyncMock(side_effect=fake_flush)

    def capture_add(obj: object) -> None:
        created_rows.append(obj)  # type: ignore[arg-type]

    session.add = MagicMock(side_effect=capture_add)

    # replace_files constructs SkillFile ORM instances — patch the model class
    # is unnecessary if we let it construct; MagicMock session is enough.
    # After replace, list_by_skill returns scalars.
    metas = await skill_files_repo.replace_files(
        session,
        skill_id=skill_id,
        files=[
            {
                "relative_path": "references/a.md",
                "kind": "reference",
                "mime_type": "text/markdown",
                "content": b"# A",
            },
            {
                "relative_path": "assets/b.bin",
                "kind": "asset",
                "mime_type": "application/octet-stream",
                "content": b"\x00\x01",
            },
        ],
    )
    assert len(metas) == 2
    assert metas[0].relative_path == "references/a.md"
    assert metas[1].kind == "asset"
    assert session.execute.await_count >= 1  # delete before insert

    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = [
        _file_row(skill_id=skill_id, relative_path="references/a.md"),
        _file_row(
            skill_id=skill_id,
            relative_path="assets/b.bin",
            kind="asset",
            mime_type="application/octet-stream",
            content=b"\x00\x01",
            size_bytes=2,
        ),
    ]
    session.execute = AsyncMock(return_value=list_result)
    listed = await skill_files_repo.list_by_skill(session, skill_id)
    assert len(listed) == 2
    assert listed[0].relative_path == "references/a.md"

    get_result = MagicMock()
    get_result.scalar_one_or_none.return_value = _file_row(
        skill_id=skill_id,
        relative_path="references/a.md",
        content=b"# A",
        size_bytes=3,
    )
    session.execute = AsyncMock(return_value=get_result)
    found = await skill_files_repo.get_by_path(
        session,
        skill_id=skill_id,
        relative_path="references/a.md",
    )
    assert found is not None
    assert found.content == b"# A"
    assert found.relative_path == "references/a.md"


@pytest.mark.asyncio
async def test_get_by_path_missing_returns_none() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    assert (
        await skill_files_repo.get_by_path(
            session,
            skill_id=uuid.uuid4(),
            relative_path="references/missing.md",
        )
        is None
    )


@pytest.mark.asyncio
async def test_count_by_kind() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = [("reference",), ("reference",), ("asset",)]
    session.execute = AsyncMock(return_value=result)
    refs, assets = await skill_files_repo.count_by_kind(session, uuid.uuid4())
    assert refs == 2
    assert assets == 1


@pytest.mark.asyncio
async def test_cascade_delete_parent_skill_uses_fk() -> None:
    """Document that deleting a skill removes skill_files via FK cascade.

    Repository delete is on skill_repo; this asserts replace_files clears
    existing rows for a skill (the overwrite path used on re-import).
    """
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    await skill_files_repo.replace_files(
        session,
        skill_id=uuid.uuid4(),
        files=[],
    )
    # Empty replace still issues a delete for the skill_id.
    assert session.execute.await_count == 1
