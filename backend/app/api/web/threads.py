"""Thread detail routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import limiter
from app.models.schemas.dashboard import AuditEntry, MessageDetail, ThreadDetail
from app.repositories import audit_repo, thread_repo
from app.services import thread_view_service

router = APIRouter(prefix="/api/threads", tags=["threads"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.get(
    "/{thread_id}",
    response_model=ThreadDetail,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def get_thread(
    thread_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
) -> ThreadDetail:
    return await thread_view_service.get_thread_detail(session, settings, thread_id)


@router.get(
    "/{thread_id}/messages",
    response_model=list[MessageDetail],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def get_thread_messages(
    thread_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
) -> list[MessageDetail]:
    return await thread_view_service.list_thread_messages(session, settings, thread_id)


@router.get(
    "/{thread_id}/audit",
    response_model=list[AuditEntry],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def get_thread_audit(
    thread_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
) -> list[AuditEntry]:
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    return await audit_repo.list_by_thread_id(session, thread_id, thread.conversation_id)
