"""Skills CRUD API — editable standing instructions for draft generation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.core.dependencies_auth import CurrentAdmin, CurrentUser
from app.core.exceptions import SkillNotFoundError
from app.core.rate_limit import limiter
from app.models.schemas.skill import (
    SkillCreateSchema,
    SkillResponseSchema,
    SkillUpdateSchema,
)
from app.repositories import skill_repo

router = APIRouter(prefix="/api/skills", tags=["skills"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=list[SkillResponseSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def list_skills(
    request: Request,
    response: Response,
    session: DbSession,
    _user: CurrentUser,
) -> list[SkillResponseSchema]:
    _ = request, response
    return await skill_repo.list_all(session)


@router.post(
    "",
    response_model=SkillResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("60/minute")
async def create_skill(
    body: SkillCreateSchema,
    request: Request,
    response: Response,
    session: DbSession,
    _admin: CurrentAdmin,
) -> SkillResponseSchema:
    _ = request, response
    # CurrentAdmin/CurrentUser already autobegins this session — do not begin again.
    created = await skill_repo.create(session, body)
    await session.commit()
    return created


@router.put(
    "/{skill_id}",
    response_model=SkillResponseSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def update_skill(
    skill_id: uuid.UUID,
    body: SkillUpdateSchema,
    request: Request,
    response: Response,
    session: DbSession,
    _admin: CurrentAdmin,
) -> SkillResponseSchema:
    _ = request, response
    updated = await skill_repo.update_skill(session, skill_id, body)
    if updated is None:
        raise SkillNotFoundError(f"Skill not found: {skill_id}")
    await session.commit()
    return updated


@router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit("60/minute")
async def delete_skill(
    skill_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    _admin: CurrentAdmin,
) -> None:
    _ = request, response
    deleted = await skill_repo.delete_skill(session, skill_id)
    if not deleted:
        raise SkillNotFoundError(f"Skill not found: {skill_id}")
    await session.commit()
