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
from app.models.schemas.related import (
    ApplyTreatmentResponse,
    ApplyTreatmentSchema,
    RelatedPurpose,
    RelatedReviewResponse,
    RelatedReviewSchema,
    RelatedThreadList,
)
from app.services import (
    draft_regeneration_service,
    related_thread_service,
    thread_view_service,
)

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


@router.get(
    "/{thread_id}/related",
    response_model=RelatedThreadList,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def get_related_threads(
    thread_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    _user: CurrentUser,
    purpose: RelatedPurpose = "siblings",
) -> RelatedThreadList:
    """Propose sibling or associated threads. Search failure returns no items."""
    _ = request, response
    result = await related_thread_service.list_related(
        session,
        settings,
        thread_id,
        purpose=purpose,
        openai_client=openai_client,
        actor=_user.email,
    )
    await session.commit()
    return result


@router.post(
    "/{thread_id}/apply-treatment",
    response_model=ApplyTreatmentResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def apply_related_treatment(
    thread_id: uuid.UUID,
    body: ApplyTreatmentSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    user: CurrentUser,
) -> ApplyTreatmentResponse:
    """Apply no-reply or urgency to a confirmed sibling subset. Local DB only."""
    _ = request, response
    applied = await related_thread_service.apply_treatment(
        session,
        settings,
        thread_id,
        treatment=body.treatment,
        thread_ids=body.thread_ids,
        reason=body.reason,
        actor=user.email,
        openai_client=openai_client,
        urgency=body.urgency,
    )
    await session.commit()
    return ApplyTreatmentResponse(applied_thread_ids=applied)


@router.post(
    "/{thread_id}/related/{related_id}/review",
    response_model=RelatedReviewResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def review_related_thread(
    thread_id: uuid.UUID,
    related_id: uuid.UUID,
    body: RelatedReviewSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
) -> RelatedReviewResponse:
    """Confirm or dismiss a proposed related thread. Local DB only."""
    _ = request, response
    result = await related_thread_service.review_related(
        session,
        settings,
        thread_id,
        related_id,
        status=body.status,
        actor=user.email,
    )
    await session.commit()
    return result
