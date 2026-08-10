"""Thread detail routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import AnthropicClientDep, OpenAIClientDep, get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import limiter
from app.models.schemas.dashboard import AuditEntry, DraftView, MessageDetail, ThreadDetail
from app.models.schemas.feedback import RegenerateDraftSchema
from app.services import draft_regeneration_service, thread_view_service

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
    return await thread_view_service.list_thread_audit(session, settings, thread_id)


@router.post(
    "/{thread_id}/regenerate-draft",
    response_model=DraftView,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def regenerate_draft(
    thread_id: uuid.UUID,
    body: RegenerateDraftSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    client: AnthropicClientDep,
    openai_client: OpenAIClientDep,
    user: CurrentUser,
) -> DraftView:
    """Regenerate a draft with a reviewer instruction. Creates a new draft row."""
    _ = request, response
    # Service commits the read txn before Sonnet, then opens a short write txn.
    persisted = await draft_regeneration_service.regenerate_draft(
        session,
        client=client,
        settings=settings,
        thread_id=thread_id,
        instruction=body.instruction,
        actor=user.email,
        openai_client=openai_client,
    )
    return thread_view_service.draft_response_to_view(persisted)
