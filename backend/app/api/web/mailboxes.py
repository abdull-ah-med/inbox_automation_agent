"""Mailbox listing and thread list routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import limiter
from app.models.schemas.dashboard import MailboxOverview, ThreadList
from app.services import dashboard_service, mailbox_service

router = APIRouter(prefix="/api/mailboxes", tags=["mailboxes"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.get(
    "",
    response_model=list[MailboxOverview],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def list_mailboxes(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
) -> list[MailboxOverview]:
    _ = request, response
    return await dashboard_service.list_mailbox_overviews(session, settings)


@router.get(
    "/{mailbox}/threads",
    response_model=ThreadList,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def list_mailbox_threads(
    mailbox: str,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
    state: Annotated[str | None, Query()] = None,
    urgency: Annotated[str | None, Query()] = None,
    stale_only: Annotated[bool, Query()] = False,
    include_filtered: Annotated[bool, Query()] = False,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ThreadList:
    _ = request, response
    return await mailbox_service.list_threads(
        session,
        settings,
        mailbox_key=mailbox,
        state=state,
        urgency=urgency,
        stale_only=stale_only,
        include_filtered=include_filtered,
        cursor=cursor,
        limit=limit,
    )
