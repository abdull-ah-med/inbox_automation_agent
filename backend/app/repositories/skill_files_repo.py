"""Skill-file repository — bundled references/assets for imported skills."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.skill_file import SkillFile
from app.models.schemas.skill import SkillFileKind, SkillFileMetaSchema


class SkillFileRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    skill_id: uuid.UUID
    relative_path: str
    kind: SkillFileKind
    mime_type: str
    size_bytes: int
    content: bytes


def _to_meta(row: SkillFile) -> SkillFileMetaSchema:
    return SkillFileMetaSchema.model_validate(row)


def _to_row(row: SkillFile) -> SkillFileRow:
    return SkillFileRow.model_validate(row)


async def replace_files(
    session: AsyncSession,
    *,
    skill_id: uuid.UUID,
    files: list[dict[str, object]],
) -> list[SkillFileMetaSchema]:
    """Delete existing files for a skill and insert the provided set."""
    await session.execute(sa_delete(SkillFile).where(SkillFile.skill_id == skill_id))
    created: list[SkillFileMetaSchema] = []
    for item in files:
        relative_path = str(item["relative_path"])
        kind = str(item["kind"])
        mime_type = str(item["mime_type"])
        content = item["content"]
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError("skill file content must be bytes")
        payload = bytes(content)
        row = SkillFile(
            skill_id=skill_id,
            relative_path=relative_path,
            kind=kind,
            mime_type=mime_type,
            size_bytes=len(payload),
            content=payload,
        )
        session.add(row)
        await session.flush()
        created.append(_to_meta(row))
    return created


async def list_by_skill(
    session: AsyncSession,
    skill_id: uuid.UUID,
) -> list[SkillFileMetaSchema]:
    stmt = (
        select(SkillFile)
        .where(SkillFile.skill_id == skill_id)
        .order_by(SkillFile.relative_path.asc())
    )
    result = await session.execute(stmt)
    return [_to_meta(row) for row in result.scalars().all()]


async def get_by_path(
    session: AsyncSession,
    *,
    skill_id: uuid.UUID,
    relative_path: str,
) -> SkillFileRow | None:
    stmt = select(SkillFile).where(
        SkillFile.skill_id == skill_id,
        SkillFile.relative_path == relative_path,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_row(row)


async def count_by_kind(
    session: AsyncSession,
    skill_id: uuid.UUID,
) -> tuple[int, int]:
    """Return (reference_count, asset_count)."""
    stmt = select(SkillFile.kind).where(SkillFile.skill_id == skill_id)
    result = await session.execute(stmt)
    kinds = [row[0] for row in result.all()]
    refs = sum(1 for kind in kinds if kind == "reference")
    assets = sum(1 for kind in kinds if kind == "asset")
    return refs, assets
