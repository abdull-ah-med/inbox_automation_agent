"""Tone profiles API — read-only list for Settings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.web.mailbox_access import require_allowed_mailbox
from app.core.dependencies import SettingsDep, get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import api_default_limit_value, limiter
from app.models.schemas.tone_profile import ToneProfileResponseSchema
from app.services import tone_profile_service

router = APIRouter(prefix="/api/tone-profiles", tags=["tone-profiles"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=list[ToneProfileResponseSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit(api_default_limit_value)
async def list_tone_profiles(
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _user: CurrentUser,
    mailbox: Annotated[str | None, Query(max_length=320)] = None,
) -> list[ToneProfileResponseSchema]:
    _ = request, response
    if mailbox is not None:
        require_allowed_mailbox(settings, mailbox)
    return await tone_profile_service.list_profiles(
        session,
        mailbox=mailbox,
        mailboxes=list(settings.mailbox_list),
    )
