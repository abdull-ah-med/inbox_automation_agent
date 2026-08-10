"""Skill CRUD orchestration for the web API (thin route → service → repo)."""

from __future__ import annotations

import uuid

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import SkillNotFoundError
from app.models.schemas.skill import (
    SkillCreateSchema,
    SkillFileMetaSchema,
    SkillResponseSchema,
    SkillUpdateSchema,
)
from app.repositories import skill_files_repo, skill_repo
from app.repositories.skill_files_repo import SkillFileRow
from app.services import skill_embedding_service


async def list_skills(session: AsyncSession) -> list[SkillResponseSchema]:
    return await skill_repo.list_all(session)


async def create_skill(
    session: AsyncSession,
    body: SkillCreateSchema,
    *,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
) -> SkillResponseSchema:
    created = await skill_repo.create(session, body)
    await skill_embedding_service.maybe_embed_skill(
        session,
        skill_id=created.id,
        name=created.name,
        description=created.description,
        body=created.content,
        settings=settings,
        openai_client=openai_client,
    )
    return created


async def update_skill(
    session: AsyncSession,
    skill_id: uuid.UUID,
    body: SkillUpdateSchema,
    *,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
) -> SkillResponseSchema:
    updated = await skill_repo.update_skill(session, skill_id, body)
    if updated is None:
        raise SkillNotFoundError(f"Skill not found: {skill_id}")
    await skill_embedding_service.maybe_embed_skill(
        session,
        skill_id=updated.id,
        name=updated.name,
        description=updated.description,
        body=updated.content,
        settings=settings,
        openai_client=openai_client,
    )
    return updated


async def delete_skill(session: AsyncSession, skill_id: uuid.UUID) -> None:
    deleted = await skill_repo.delete_skill(session, skill_id)
    if not deleted:
        raise SkillNotFoundError(f"Skill not found: {skill_id}")


async def list_skill_files(
    session: AsyncSession,
    skill_id: uuid.UUID,
) -> list[SkillFileMetaSchema]:
    skill = await skill_repo.get_by_id(session, skill_id)
    if skill is None:
        raise SkillNotFoundError(f"Skill not found: {skill_id}")
    return await skill_files_repo.list_by_skill(session, skill_id)


async def get_skill_file(
    session: AsyncSession,
    skill_id: uuid.UUID,
    relative_path: str,
) -> SkillFileRow:
    skill = await skill_repo.get_by_id(session, skill_id)
    if skill is None:
        raise SkillNotFoundError(f"Skill not found: {skill_id}")
    row = await skill_files_repo.get_by_path(
        session,
        skill_id=skill_id,
        relative_path=relative_path,
    )
    if row is None:
        raise SkillNotFoundError(f"File not found: {relative_path}")
    return row
