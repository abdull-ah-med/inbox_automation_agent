"""Skill candidates API — list / accept / dismiss proposed skills."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import OpenAIClientDep, get_db
from app.core.dependencies_auth import CurrentAdmin, CurrentUser
from app.core.rate_limit import limiter
from app.models.schemas.skill import SkillResponseSchema
from app.models.schemas.skill_candidate import SkillCandidateResponseSchema
from app.services import skill_candidate_service

router = APIRouter(prefix="/api/skill-candidates", tags=["skill-candidates"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.get(
    "",
    response_model=list[SkillCandidateResponseSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def list_skill_candidates(
    request: Request,
    response: Response,
    session: DbSession,
    _user: CurrentUser,
    mailbox: Annotated[str | None, Query(max_length=320)] = None,
) -> list[SkillCandidateResponseSchema]:
    _ = request, response
    return await skill_candidate_service.list_pending(session, mailbox=mailbox)


@router.post(
    "/{candidate_id}/accept",
    response_model=SkillResponseSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def accept_skill_candidate(
    candidate_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    _admin: CurrentAdmin,
) -> SkillResponseSchema:
    _ = request, response
    skill = await skill_candidate_service.accept_candidate(
        session,
        candidate_id,
        settings=settings,
        openai_client=openai_client,
    )
    await session.commit()
    return skill


@router.post(
    "/{candidate_id}/dismiss",
    response_model=SkillCandidateResponseSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def dismiss_skill_candidate(
    candidate_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    _admin: CurrentAdmin,
) -> SkillCandidateResponseSchema:
    _ = request, response
    updated = await skill_candidate_service.dismiss_candidate(session, candidate_id)
    await session.commit()
    return updated
