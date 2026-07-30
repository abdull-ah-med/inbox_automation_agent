"""Draft feedback API — approve / reject / mark-wrong (never sends email)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import OpenAIClientDep, get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import limiter
from app.models.schemas.dashboard import DraftView
from app.models.schemas.feedback import DraftApproveSchema, DraftRejectSchema
from app.services import draft_feedback_service, thread_view_service

router = APIRouter(prefix="/api/drafts", tags=["drafts"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/{draft_id}/approve",
    response_model=DraftView,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def approve_draft(
    draft_id: uuid.UUID,
    body: DraftApproveSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    user: CurrentUser,
) -> DraftView:
    """Approve a draft (optionally with edited body). Does not send email."""
    _ = request, response
    async with session.begin():
        updated = await draft_feedback_service.approve_draft(
            session,
            draft_id,
            edited_body=body.edited_body,
            actor=user.email,
            settings=settings,
        )

    # Embed after the approve txn commits so we never hold Postgres during OpenAI.
    await draft_feedback_service.store_approved_reply_memory(
        session,
        draft=updated,
        settings=settings,
        openai_client=openai_client,
    )
    return thread_view_service.draft_response_to_view(updated)


@router.post(
    "/{draft_id}/reject",
    response_model=DraftView,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def reject_draft(
    draft_id: uuid.UUID,
    body: DraftRejectSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
) -> DraftView:
    """Reject a draft with a required note. Does not send email."""
    _ = request, response
    async with session.begin():
        updated = await draft_feedback_service.reject_draft(
            session,
            draft_id,
            feedback_note=body.feedback_note,
            actor=user.email,
            settings=settings,
        )
    return thread_view_service.draft_response_to_view(updated)


@router.post(
    "/{draft_id}/wrong",
    response_model=DraftView,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def mark_draft_wrong(
    draft_id: uuid.UUID,
    body: DraftRejectSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
) -> DraftView:
    """Mark a draft as wrong with a required note. Does not send email."""
    _ = request, response
    async with session.begin():
        updated = await draft_feedback_service.mark_wrong(
            session,
            draft_id,
            feedback_note=body.feedback_note,
            actor=user.email,
            settings=settings,
        )
    return thread_view_service.draft_response_to_view(updated)
