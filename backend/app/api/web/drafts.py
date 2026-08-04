"""Draft feedback API — approve / reject / mark-wrong (never sends email)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import AnthropicClientDep, OpenAIClientDep, get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import limiter
from app.models.schemas.dashboard import DraftView
from app.models.schemas.feedback import DraftApproveSchema, DraftRejectSchema, DraftWrongSchema
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
    anthropic_client: AnthropicClientDep,
    user: CurrentUser,
) -> DraftView:
    """Approve a draft (optionally with edited body). Does not send email."""
    _ = request, response
    # CurrentUser already autobegins this session — do not session.begin() again.
    updated = await draft_feedback_service.approve_draft(
        session,
        draft_id,
        edited_body=body.edited_body,
        actor=user.email,
        settings=settings,
    )
    await session.commit()

    # Dedicated session inside the helper — never poison this request session.
    # https://docs.sqlalchemy.org/en/20/errors.html#this-session-s-transaction-has-been-rolled-back-due-to-a-previous-exception-during-flush
    await draft_feedback_service.store_approved_reply_memory(
        draft=updated,
        settings=settings,
        openai_client=openai_client,
        anthropic_client=anthropic_client,
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
    openai_client: OpenAIClientDep,
    anthropic_client: AnthropicClientDep,
    user: CurrentUser,
) -> DraftView:
    """Reject a draft with a required note. Does not send email."""
    _ = request, response
    updated = await draft_feedback_service.reject_draft(
        session,
        draft_id,
        feedback_note=body.feedback_note,
        reason_code=body.reason_code,
        actor=user.email,
        settings=settings,
    )
    await session.commit()

    await draft_feedback_service.store_rejection_memory(
        draft=updated,
        settings=settings,
        openai_client=openai_client,
        anthropic_client=anthropic_client,
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
    body: DraftWrongSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
) -> DraftView:
    """Mark a draft as wrong with a required note. Does not send email."""
    _ = request, response
    updated = await draft_feedback_service.mark_wrong(
        session,
        draft_id,
        feedback_note=body.feedback_note,
        reason_code=body.reason_code or "other",
        actor=user.email,
        settings=settings,
    )
    await session.commit()
    return thread_view_service.draft_response_to_view(updated)
