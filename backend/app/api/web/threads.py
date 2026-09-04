"""Thread detail routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    AnthropicClientDep,
    GraphClientDep,
    OpenAIClientDep,
    RedisDep,
    get_db,
)
from app.core.dependencies_auth import CurrentUser
from app.core.exceptions import ThreadNotFoundError
from app.core.rate_limit import context_rebuild_rate_limit_key, limiter
from app.core.tenant_scope import TenantScope
from app.models.schemas.dashboard import (
    AuditEntry,
    DraftView,
    MessageDetail,
    MessageHtmlBody,
    ThreadDetail,
    ThreadHeader,
)
from app.models.schemas.feedback import RegenerateDraftSchema
from app.models.schemas.related import (
    ApplyTreatmentResponse,
    ApplyTreatmentSchema,
    RelatedPurpose,
    RelatedReviewResponse,
    RelatedReviewSchema,
    RelatedThreadList,
)
from app.models.schemas.resolution import (
    ResolutionFeedbackResponse,
    ResolutionFeedbackSchema,
    ResolveThreadResponse,
    ResolveThreadSchema,
)
from app.models.schemas.spam import NotSpamResponseSchema
from app.models.schemas.thread_context import (
    ThreadContextNotesUpdate,
    ThreadContextView,
)
from app.models.schemas.urgency_hitl import (
    UrgencyHitlFeedbackResponse,
    UrgencyHitlFeedbackSchema,
)
from app.repositories import thread_repo
from app.services import (
    draft_regeneration_service,
    message_html_service,
    not_spam_service,
    recurrence_service,
    related_thread_service,
    resolution_service,
    thread_context_service,
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
    _ = request, response
    return await thread_view_service.get_thread_detail(session, settings, thread_id)


@router.get(
    "/{thread_id}/header",
    response_model=ThreadHeader,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def get_thread_header(
    thread_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
) -> ThreadHeader:
    _ = request, response
    return await thread_view_service.get_thread_header(session, settings, thread_id)


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
    _ = request, response
    return await thread_view_service.list_thread_messages(session, settings, thread_id)


@router.get(
    "/{thread_id}/messages/{message_id}/html",
    response_model=MessageHtmlBody,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("30/minute")
async def get_message_html(
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    graph_client: GraphClientDep,
    _user: CurrentUser,
) -> MessageHtmlBody:
    """On-demand Graph HTML for Outlook View. Not persisted; Mail.Read only."""
    _ = request, response
    return await message_html_service.get_message_html(
        session,
        settings,
        graph_client,
        thread_id,
        message_id,
    )


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
    _ = request, response
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
    graph_client: GraphClientDep,
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
        graph_client=graph_client,
    )
    return thread_view_service.draft_response_to_view(persisted)


@router.post(
    "/{thread_id}/generate-draft",
    response_model=DraftView,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def generate_draft(
    thread_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    client: AnthropicClientDep,
    openai_client: OpenAIClientDep,
    graph_client: GraphClientDep,
    user: CurrentUser,
) -> DraftView:
    """Generate a letter draft on a briefing thread. Creates a new draft row."""
    _ = request, response
    persisted = await draft_regeneration_service.generate_draft(
        session,
        client=client,
        settings=settings,
        thread_id=thread_id,
        actor=user.email,
        openai_client=openai_client,
        graph_client=graph_client,
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


@router.post(
    "/{thread_id}/resolve",
    response_model=ResolveThreadResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def resolve_thread(
    thread_id: uuid.UUID,
    body: ResolveThreadSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
    anthropic_client: AnthropicClientDep,
    openai_client: OpenAIClientDep,
) -> ResolveThreadResponse:
    """Mark a thread resolved (local DB only). Does not send or edit Outlook mail."""
    _ = request, response
    state = await resolution_service.resolve_thread_manual(
        session,
        settings,
        thread_id,
        actor=user.email,
        actions_taken=body.actions_taken,
        involved=body.involved,
        anthropic_client=anthropic_client,
        openai_client=openai_client,
    )
    await session.commit()
    return ResolveThreadResponse(state=state)


@router.post(
    "/{thread_id}/resolution-feedback",
    response_model=ResolutionFeedbackResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def resolution_feedback(
    thread_id: uuid.UUID,
    body: ResolutionFeedbackSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
) -> ResolutionFeedbackResponse:
    """Reopen a finished thread or record a wrong auto-resolve reason."""
    _ = request, response
    state = await resolution_service.apply_resolution_feedback(
        session,
        settings,
        thread_id,
        action=body.action,
        actor=user.email,
        note=body.note,
    )
    await session.commit()
    return ResolutionFeedbackResponse(state=state, action=body.action)


@router.post(
    "/{thread_id}/urgency-feedback",
    response_model=UrgencyHitlFeedbackResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def urgency_feedback(
    thread_id: uuid.UUID,
    body: UrgencyHitlFeedbackSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
) -> UrgencyHitlFeedbackResponse:
    """Mark an automatic urgency bump wrong; revert this thread and suppress fingerprint."""
    _ = request, response
    thread = await thread_repo.get_by_id(session, thread_id, TenantScope.from_settings(settings))
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise ThreadNotFoundError(f"Thread not found: {thread_id}")
    urgency = await recurrence_service.apply_urgency_feedback(
        session,
        thread_id=thread_id,
        action=body.action,
        actor=user.email,
        note=body.note,
    )
    await session.commit()
    return UrgencyHitlFeedbackResponse(
        state=thread.state,
        action=body.action,
        urgency=urgency,
    )


@router.post(
    "/{thread_id}/not-spam",
    response_model=NotSpamResponseSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("20/minute")
async def mark_thread_not_spam(
    thread_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    redis: RedisDep,
    client: AnthropicClientDep,
    openai_client: OpenAIClientDep,
    user: CurrentUser,
) -> NotSpamResponseSchema:
    """Clear a spam false positive and allowlist the sender. Does not move Outlook mail."""
    _ = request, response
    return await not_spam_service.mark_not_spam(
        session,
        settings=settings,
        thread_id=thread_id,
        actor=user.email,
        redis=redis,
        client=client,
        openai_client=openai_client,
    )


async def _require_context_thread(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
) -> None:
    if not settings.thread_context_enabled:
        raise ThreadNotFoundError(f"Thread not found: {thread_id}")
    thread = await thread_repo.get_by_id(session, thread_id, TenantScope.from_settings(settings))
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise ThreadNotFoundError(f"Thread not found: {thread_id}")


async def _run_context_rebuild(thread_id: uuid.UUID, settings: Settings) -> None:
    from app.core.dependencies import anthropic_client_from_settings
    from app.db.session import get_session_factory

    try:
        factory = get_session_factory()
        client = anthropic_client_from_settings(settings)
        async with factory() as session:
            outcome = await thread_context_service.extract_if_needed(
                session,
                thread_id,
                client=client,
                settings=settings,
                source="rebuild",
                force=True,
            )
            await thread_context_service.complete_rebuild_after_extract(session, thread_id, outcome)
            if session.in_transaction():
                await session.commit()
    except Exception:
        try:
            factory = get_session_factory()
            async with factory() as session:
                await thread_context_service.finish_rebuild(
                    session,
                    thread_id,
                    error="Rebuild failed. Try again.",
                )
                if session.in_transaction():
                    await session.commit()
        except Exception:
            return


@router.get(
    "/{thread_id}/context",
    response_model=ThreadContextView,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def get_thread_context(
    thread_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
) -> ThreadContextView:
    _ = request, response
    await _require_context_thread(session, settings, thread_id)
    return await thread_context_service.present_context(session, thread_id)


@router.put(
    "/{thread_id}/context/user_notes",
    response_model=ThreadContextView,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def put_thread_context_user_notes(
    thread_id: uuid.UUID,
    body: ThreadContextNotesUpdate,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
) -> ThreadContextView:
    _ = request, response
    await _require_context_thread(session, settings, thread_id)
    await thread_context_service.save_user_notes(
        session,
        thread_id,
        notes=body.user_notes,
        expected_version=body.expected_version,
    )
    view = await thread_context_service.present_context(session, thread_id)
    await session.commit()
    return view


@router.post(
    "/{thread_id}/context/rebuild",
    response_model=ThreadContextView,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("12/minute", key_func=context_rebuild_rate_limit_key)
async def rebuild_thread_context(
    thread_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    background_tasks: BackgroundTasks,
    _user: CurrentUser,
) -> ThreadContextView:
    _ = request, response
    await _require_context_thread(session, settings, thread_id)
    spawned = await thread_context_service.begin_rebuild(session, thread_id)
    await session.commit()
    if spawned:
        background_tasks.add_task(_run_context_rebuild, thread_id, settings)
    return await thread_context_service.present_context(session, thread_id)


@router.delete(
    "/{thread_id}/context/facts/{fact_id}",
    response_model=ThreadContextView,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def discard_thread_context_fact(
    thread_id: uuid.UUID,
    fact_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
) -> ThreadContextView:
    _ = request, response
    await _require_context_thread(session, settings, thread_id)
    await thread_context_service.discard_fact(session, thread_id, fact_id)
    view = await thread_context_service.present_context(session, thread_id)
    await session.commit()
    return view
