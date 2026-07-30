"""Draft feedback orchestration — approve / reject / mark-wrong + audit."""

from __future__ import annotations

import uuid

import structlog
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import DraftNotFoundError
from app.models.schemas.draft import DraftResponseSchema
from app.repositories import draft_repo, thread_repo
from app.services import audit_service, reply_memory_service

logger = structlog.get_logger(__name__)


async def _require_thread_meta(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    settings: Settings | None,
) -> tuple[str, str]:
    """Return (conversation_id, mailbox) or raise DraftNotFoundError.

    Enforces TARGET_MAILBOXES allowlist when settings are provided (API path).
    """
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None:
        raise DraftNotFoundError(f"Draft thread not found: {thread_id}")
    if settings is not None and not settings.mailbox_allowed(thread.mailbox):
        raise DraftNotFoundError(f"Draft not found for thread: {thread_id}")
    return (thread.conversation_id, thread.mailbox)


async def approve_draft(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    edited_body: str | None = None,
    actor: str = "user",
    settings: Settings | None = None,
) -> DraftResponseSchema:
    """Approve a draft (optionally with an edited body). Idempotent.

    Does not send email — status + audit only.
    Reply-memory embedding is intentionally NOT done here so the caller can
    commit the approval txn before any OpenAI network call.
    """
    existing = await draft_repo.get_draft_by_id(session, draft_id)
    if existing is None:
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    conversation_id, mailbox = await _require_thread_meta(
        session,
        existing.thread_id,
        settings=settings,
    )

    already_approved = existing.approved_at is not None and existing.feedback_action == "approve"
    if already_approved and (
        edited_body is None or edited_body == (existing.edited_body or existing.reply_body)
    ):
        return existing

    updated = await draft_repo.approve_draft(
        session,
        draft_id,
        edited_body=edited_body,
    )
    if updated is None:
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    event_type = "draft.edited_and_approved" if edited_body is not None else "draft.approved"
    try:
        await audit_service.log_event(
            session,
            event_type=event_type,
            conversation_id=conversation_id,
            mailbox=mailbox,
            payload={
                "draft_id": str(draft_id),
                "feedback_action": "approve",
                "had_edit": edited_body is not None,
            },
            actor=actor,
        )
    except Exception:
        logger.warning(
            "draft_feedback_audit_failed",
            draft_id=str(draft_id),
            event_type=event_type,
        )

    logger.info(
        "draft_approved",
        draft_id=str(draft_id),
        had_edit=edited_body is not None,
        actor=actor,
        mailbox=mailbox,
    )
    return updated


async def store_approved_reply_memory(
    session: AsyncSession,
    *,
    draft: DraftResponseSchema,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
) -> None:
    """Best-effort reply memory after the approve txn has committed.

    Resolves mailbox, commits any open read txn, embeds (network), then inserts.
    """
    thread = await thread_repo.get_by_id(session, draft.thread_id)
    mailbox = thread.mailbox if thread is not None else "unknown"
    if session.in_transaction():
        await session.commit()
    final_body = draft.edited_body or draft.reply_body
    await reply_memory_service.store_approved_reply(
        session,
        openai_client=openai_client,
        settings=settings,
        draft_id=draft.id,
        mailbox=mailbox,
        final_body=final_body,
    )
    if session.in_transaction():
        await session.commit()


async def reject_draft(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    feedback_note: str,
    actor: str = "user",
    settings: Settings | None = None,
) -> DraftResponseSchema:
    """Reject a draft with a required note. Idempotent. Does not send email."""
    existing = await draft_repo.get_draft_by_id(session, draft_id)
    if existing is None:
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    conversation_id, mailbox = await _require_thread_meta(
        session,
        existing.thread_id,
        settings=settings,
    )

    if existing.rejected_at is not None and existing.feedback_action == "reject":
        return existing

    updated = await draft_repo.reject_draft(
        session,
        draft_id,
        feedback_note=feedback_note,
    )
    if updated is None:
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    try:
        await audit_service.log_event(
            session,
            event_type="draft.rejected",
            conversation_id=conversation_id,
            mailbox=mailbox,
            payload={
                "draft_id": str(draft_id),
                "feedback_action": "reject",
            },
            actor=actor,
        )
    except Exception:
        logger.warning(
            "draft_feedback_audit_failed",
            draft_id=str(draft_id),
            event_type="draft.rejected",
        )

    logger.info("draft_rejected", draft_id=str(draft_id), actor=actor)
    return updated


async def mark_wrong(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    feedback_note: str,
    actor: str = "user",
    settings: Settings | None = None,
) -> DraftResponseSchema:
    """Mark a draft as wrong with a required note. Idempotent. Does not send email."""
    existing = await draft_repo.get_draft_by_id(session, draft_id)
    if existing is None:
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    conversation_id, mailbox = await _require_thread_meta(
        session,
        existing.thread_id,
        settings=settings,
    )

    if existing.feedback_action == "wrong":
        return existing

    updated = await draft_repo.mark_wrong(
        session,
        draft_id,
        feedback_note=feedback_note,
    )
    if updated is None:
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    try:
        await audit_service.log_event(
            session,
            event_type="draft.marked_wrong",
            conversation_id=conversation_id,
            mailbox=mailbox,
            payload={
                "draft_id": str(draft_id),
                "feedback_action": "wrong",
            },
            actor=actor,
        )
    except Exception:
        logger.warning(
            "draft_feedback_audit_failed",
            draft_id=str(draft_id),
            event_type="draft.marked_wrong",
        )

    logger.info("draft_marked_wrong", draft_id=str(draft_id), actor=actor)
    return updated
