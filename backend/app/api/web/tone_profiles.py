"""Tone profiles API — read-only list for Settings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import limiter
from app.models.schemas.tone_profile import ToneProfileResponseSchema
from app.services import tone_profile_service

router = APIRouter(prefix="/api/tone-profiles", tags=["tone-profiles"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=list[ToneProfileResponseSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def list_tone_profiles(
    request: Request,
    response: Response,
    session: DbSession,
    _user: CurrentUser,
    mailbox: Annotated[str | None, Query(max_length=320)] = None,
) -> list[ToneProfileResponseSchema]:
    _ = request, response
    return await tone_profile_service.list_profiles(session, mailbox=mailbox)
