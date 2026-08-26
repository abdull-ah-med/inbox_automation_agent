"""Manual urgency edit orchestration + retrieval for draft-time RAG."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import DraftNotFoundError, ThreadStateError
from app.core.tenant_scope import TenantScope
from app.db.session import get_session_factory
from app.llm.pii_redact import scrub_text
from app.models.schemas.urgency_feedback import (
    UrgencyEditRequestSchema,
    UrgencyEditResponseSchema,
    UrgencyLevel,
)
from app.repositories import draft_repo, thread_repo, urgency_feedback_repo
from app.services import audit_service, embedding_service

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class UrgencyEditResult:
    """HTTP response plus values needed for post-commit memory storage."""

    response: UrgencyEditResponseSchema
    previous_urgency: UrgencyLevel
    mailbox: str
    routing_category: str | None


async def apply_manual_urgency_edit(
    session: AsyncSession,
    draft_id: uuid.UUID,
    request: UrgencyEditRequestSchema,
    *,
    actor: str,
    user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> UrgencyEditResult:
    """Update draft + thread urgency and return the new values.

    Embedding persistence is intentionally deferred to
    ``store_urgency_feedback_memory`` so the HTTP txn commits first.
    """
    _ = user_id
    draft = await draft_repo.get_draft_by_id(session, draft_id)
    if draft is None:
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    thread = await thread_repo.get_by_id(
        session, draft.thread_id, TenantScope.for_request(settings)
    )
    if thread is None:
        raise DraftNotFoundError(f"Draft thread not found: {draft.thread_id}")
    if settings is not None and not settings.mailbox_allowed(thread.mailbox):
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    previous = draft.urgency
    if previous == request.new_urgency and (draft.urgency_reason or "") == request.reason:
        return UrgencyEditResult(
            response=UrgencyEditResponseSchema(
                urgency=request.new_urgency,
                urgency_reason=request.reason,
                updated_at=datetime.now(UTC),
                draft_id=draft.id,
                thread_id=draft.thread_id,
            ),
            previous_urgency=previous,
            mailbox=thread.mailbox,
            routing_category=draft.routing_category,
        )

    updated_draft = await draft_repo.set_urgency(
        session,
        draft_id,
        urgency=request.new_urgency,
        urgency_reason=request.reason,
    )
    if updated_draft is None:
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    updated_thread = await thread_repo.set_urgency(
        session,
        draft.thread_id,
        urgency=request.new_urgency,
        urgency_reason=request.reason,
    )
    if updated_thread is None:
        raise ThreadStateError(f"Thread not found for draft: {draft_id}")

    try:
        await audit_service.log_event(
            session,
            event_type="thread.urgency.edited",
            conversation_id=thread.conversation_id,
            mailbox=thread.mailbox,
            payload={
                "draft_id": str(draft_id),
                "thread_id": str(draft.thread_id),
                "previous": previous,
                "new": request.new_urgency,
                "mailbox": thread.mailbox,
                "has_reason": True,
            },
            actor=actor,
        )
    except Exception:
        logger.warning(
            "urgency_edit_audit_failed",
            draft_id=str(draft_id),
        )

    logger.info(
        "urgency_edited",
        draft_id=str(draft_id),
        thread_id=str(draft.thread_id),
        previous=previous,
        new=request.new_urgency,
        actor=actor,
        mailbox=thread.mailbox,
    )
    return UrgencyEditResult(
        response=UrgencyEditResponseSchema(
            urgency=request.new_urgency,
            urgency_reason=request.reason,
            updated_at=datetime.now(UTC),
            draft_id=draft.id,
            thread_id=draft.thread_id,
        ),
        previous_urgency=previous,
        mailbox=thread.mailbox,
        routing_category=draft.routing_category,
    )


async def store_urgency_feedback_memory(
    *,
    draft_id: uuid.UUID,
    thread_id: uuid.UUID,
    mailbox: str,
    routing_category: str | None,
    previous_urgency: str | None,
    new_urgency: str,
    reason: str,
    edited_by_user_id: uuid.UUID | None,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
) -> None:
    """Best-effort embed + persist urgency feedback. Never raises to HTTP."""
    if openai_client is None or not settings.openai_api_key.strip():
        logger.info(
            "urgency_feedback_store_skipped_no_openai",
            draft_id=str(draft_id),
            mailbox=mailbox,
        )
        return

    try:
        scrubbed = scrub_text(reason)
        if not scrubbed.strip():
            return
        embed_text = f"{new_urgency}: {scrubbed}"
        vector = await embedding_service.embed_text(
            embed_text,
            client=openai_client,
            settings=settings,
        )
    except Exception:
        logger.exception(
            "urgency_feedback_embed_failed",
            draft_id=str(draft_id),
            mailbox=mailbox,
        )
        return

    try:
        factory = get_session_factory()
        async with factory() as session:
            await urgency_feedback_repo.insert_urgency_feedback(
                session,
                draft_id=draft_id,
                thread_id=thread_id,
                mailbox=mailbox,
                routing_category=routing_category,
                previous_urgency=previous_urgency,
                new_urgency=new_urgency,
                reason=scrubbed,
                embedding=vector,
                edited_by_user_id=edited_by_user_id,
            )
            if session.in_transaction():
                await session.commit()
        logger.info(
            "urgency_feedback_stored",
            draft_id=str(draft_id),
            mailbox=mailbox,
            new_urgency=new_urgency,
        )
    except Exception:
        logger.exception(
            "urgency_feedback_store_failed",
            draft_id=str(draft_id),
            mailbox=mailbox,
        )


async def find_urgency_hints(
    session: AsyncSession,
    *,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    email_text: str,
    mailbox: str,
    routing_category: str | None = None,
    limit: int = 3,
) -> list[str]:
    """Retrieve similar urgency-edit hints. Returns [] on any failure."""
    if openai_client is None or not settings.openai_api_key.strip():
        return []

    try:
        scrubbed = scrub_text(email_text)
        if not scrubbed.strip():
            return []
        vector = await embedding_service.embed_text(
            scrubbed,
            client=openai_client,
            settings=settings,
        )
    except Exception:
        logger.exception("urgency_feedback_find_failed", mailbox=mailbox)
        return []

    try:
        return await urgency_feedback_repo.find_similar(
            session,
            query_embedding=vector,
            mailbox=mailbox,
            routing_category=routing_category,
            limit=limit,
            min_similarity=settings.urgency_feedback_min_similarity,
        )
    except Exception:
        logger.exception("urgency_feedback_find_failed", mailbox=mailbox)
        return []
