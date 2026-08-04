"""Skills CRUD API — editable standing instructions for draft generation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import OpenAIClientDep, get_db
from app.core.dependencies_auth import CurrentAdmin, CurrentUser
from app.core.exceptions import SkillBudgetExceededError, SkillNameConflictError, SkillNotFoundError
from app.core.rate_limit import limiter
from app.models.schemas.skill import (
    SkillCreateSchema,
    SkillResponseSchema,
    SkillUpdateSchema,
)
from app.repositories import skill_repo
from app.services import skill_embedding_service

router = APIRouter(prefix="/api/skills", tags=["skills"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


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
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    _admin: CurrentAdmin,
) -> SkillResponseSchema:
    _ = request, response
    # CurrentAdmin/CurrentUser already autobegins this session — do not begin again.
    try:
        created = await skill_repo.create(session, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SkillBudgetExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SkillNameConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await skill_embedding_service.maybe_embed_skill(
        session,
        skill_id=created.id,
        name=created.name,
        description=created.description,
        settings=settings,
        openai_client=openai_client,
    )
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
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    _admin: CurrentAdmin,
) -> SkillResponseSchema:
    _ = request, response
    try:
        updated = await skill_repo.update_skill(session, skill_id, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SkillBudgetExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SkillNameConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if updated is None:
        raise SkillNotFoundError(f"Skill not found: {skill_id}")
    await skill_embedding_service.maybe_embed_skill(
        session,
        skill_id=updated.id,
        name=updated.name,
        description=updated.description,
        settings=settings,
        openai_client=openai_client,
    )
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
