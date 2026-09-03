"""Draft feedback orchestration — approve / reject / mark-wrong + audit."""

from __future__ import annotations

import uuid

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import DraftNotFoundError
from app.core.draftassistant_resolve import RESOLUTION_SUMMARIES, build_resolve_snapshot
from app.core.tenant_scope import TenantScope
from app.db.session import get_session_factory
from app.models.schemas.draft import DraftResponseSchema
from app.models.schemas.email import ThreadStateEnum
from app.repositories import draft_repo, thread_repo
from app.services import (
    audit_service,
    rejection_memory_service,
    reply_memory_service,
    skill_candidate_service,
    tone_profile_service,
)

logger = structlog.get_logger(__name__)


async def _load_thread(
    session: AsyncSession,
    thread_id: uuid.UUID,
    settings: Settings | None,
) -> thread_repo.ThreadSchema | None:
    """Load a thread under tenant scope when settings are real Settings."""
    if isinstance(settings, Settings):
        return await thread_repo.get_by_id(
            session,
            thread_id,
            TenantScope.from_settings(settings),
        )
    return await thread_repo.get_by_id_trusted(session, thread_id)


async def _require_thread_meta(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    settings: Settings | None,
) -> tuple[str, str]:
    """Return (conversation_id, mailbox) or raise DraftNotFoundError.

    Enforces TARGET_MAILBOXES allowlist when settings are provided (API path).
    """
    thread = await _load_thread(session, thread_id, settings)
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
    approval_note: str | None = None,
    approval_scope: str | None = None,
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
    learning_unchanged = (
        approval_note == existing.approval_note and approval_scope == existing.approval_scope
    )
    if (
        already_approved
        and (edited_body is None or edited_body == (existing.edited_body or existing.reply_body))
        and learning_unchanged
    ):
        return existing

    updated = await draft_repo.approve_draft(
        session,
        draft_id,
        edited_body=edited_body,
        approval_note=approval_note,
        approval_scope=approval_scope,
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
        if approval_note is not None or approval_scope is not None:
            await audit_service.log_event(
                session,
                event_type="draft.approved.learning_context",
                conversation_id=conversation_id,
                mailbox=mailbox,
                payload={
                    "draft_id": str(draft_id),
                    "scope": approval_scope,
                    "has_note": bool(approval_note),
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
        approval_scope=approval_scope,
        has_approval_note=bool(approval_note),
        actor=actor,
        mailbox=mailbox,
    )
    return updated


async def store_approved_reply_memory(
    *,
    draft: DraftResponseSchema,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    anthropic_client: AsyncAnthropic | None = None,
) -> None:
    """Best-effort reply memory after the approve txn has committed.

    Stores ``reply_embeddings`` only when ``approval_scope == "similar"``.
    Scope ``once`` or ``None`` (plain approve) skips embedding per the
    five-feature plan.

    Uses a **dedicated** DB session so embed/DB failures cannot leave the
    request session in SQLAlchemy's inactive/pending-rollback state.
    Never raises to the approve HTTP path.
    """
    if draft.approval_scope != "similar":
        mailbox = "unknown"
        try:
            factory = get_session_factory()
            async with factory() as session:
                thread = await _load_thread(session, draft.thread_id, settings)
                mailbox = thread.mailbox if thread is not None else "unknown"
                if session.in_transaction():
                    await session.commit()
        except Exception:
            logger.exception(
                "reply_memory_skip_mailbox_lookup_failed",
                draft_id=str(draft.id),
                approval_scope=draft.approval_scope,
            )
        logger.info(
            "reply_memory_skipped_scope",
            draft_id=str(draft.id),
            mailbox=mailbox,
            approval_scope=draft.approval_scope,
        )
        await tone_profile_service.maybe_rebuild(
            settings=settings,
            anthropic_client=anthropic_client,
            mailbox=mailbox,
            routing_category=draft.routing_category,
        )
        return

    mailbox = "unknown"
    try:
        factory = get_session_factory()
        async with factory() as session:
            thread = await _load_thread(session, draft.thread_id, settings)
            mailbox = thread.mailbox if thread is not None else "unknown"
            email_preview = thread.subject if thread is not None else None
            if session.in_transaction():
                await session.commit()
            final_body = draft.edited_body or draft.reply_body
            learning_note = draft.approval_note
            await reply_memory_service.store_approved_reply(
                session,
                openai_client=openai_client,
                settings=settings,
                draft_id=draft.id,
                mailbox=mailbox,
                final_body=final_body,
                email_preview=email_preview,
                learning_note=learning_note,
            )
            if session.in_transaction():
                await session.commit()
    except Exception:
        logger.exception(
            "reply_memory_post_approve_failed",
            draft_id=str(draft.id),
        )

    await tone_profile_service.maybe_rebuild(
        settings=settings,
        anthropic_client=anthropic_client,
        mailbox=mailbox,
        routing_category=draft.routing_category,
    )


async def store_rejection_memory(
    *,
    draft: DraftResponseSchema,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    anthropic_client: AsyncAnthropic | None = None,
) -> None:
    """Best-effort rejection memory after the reject txn has committed.

    Dedicated session (same pattern as reply memory). Only for reject — not wrong.
    Never raises to the reject HTTP path.
    """
    if not draft.feedback_note or not draft.feedback_reason_code:
        return
    mailbox = "unknown"
    routing_category = draft.routing_category or "general"
    reason_code = draft.feedback_reason_code
    try:
        factory = get_session_factory()
        async with factory() as session:
            thread = await _load_thread(session, draft.thread_id, settings)
            mailbox = thread.mailbox if thread is not None else "unknown"
            if session.in_transaction():
                await session.commit()
            await rejection_memory_service.store_rejection(
                session,
                openai_client=openai_client,
                settings=settings,
                draft_id=draft.id,
                mailbox=mailbox,
                routing_category=routing_category,
                reason_code=reason_code,
                note=draft.feedback_note,
            )
            if session.in_transaction():
                await session.commit()
    except Exception:
        logger.exception(
            "rejection_memory_post_reject_failed",
            draft_id=str(draft.id),
        )
        return

    await skill_candidate_service.maybe_propose_from_rejects(
        settings=settings,
        anthropic_client=anthropic_client,
        mailbox=mailbox,
        routing_category=routing_category,
        reason_code=reason_code,
    )


async def reject_draft(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    feedback_note: str,
    reason_code: str,
    actor: str = "user",
    settings: Settings | None = None,
) -> DraftResponseSchema:
    """Reject a draft with a required note and reason code. Idempotent. Does not send email."""
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
        reason_code=reason_code,
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
                "reason_code": reason_code,
            },
            actor=actor,
        )
    except Exception:
        logger.warning(
            "draft_feedback_audit_failed",
            draft_id=str(draft_id),
            event_type="draft.rejected",
        )

    logger.info(
        "draft_rejected",
        draft_id=str(draft_id),
        actor=actor,
        reason_code=reason_code,
    )
    return updated


async def mark_wrong(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    feedback_note: str,
    reason_code: str = "other",
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
        reason_code=reason_code,
    )
    if updated is None:
        raise DraftNotFoundError(f"Draft not found: {draft_id}")

    if reason_code == "wrong_action":
        await thread_repo.set_thread_outcome(
            session,
            existing.thread_id,
            state=ThreadStateEnum.RESOLVED.value,
        )

    try:
        await audit_service.log_event(
            session,
            event_type="draft.marked_wrong",
            conversation_id=conversation_id,
            mailbox=mailbox,
            payload={
                "draft_id": str(draft_id),
                "feedback_action": "wrong",
                "reason_code": reason_code,
            },
            actor=actor,
        )
    except Exception:
        logger.warning(
            "draft_feedback_audit_failed",
            draft_id=str(draft_id),
            event_type="draft.marked_wrong",
        )

    if reason_code == "wrong_action":
        snapshot = build_resolve_snapshot(
            resolved_by="elise",
            resolution_reason="wrong_action",
            disposition_at_resolve="resolved_elise",
            had_draft=True,
            had_letter=bool((existing.reply_body or "").strip()),
            actions_taken=feedback_note,
        )
        try:
            await audit_service.log_event(
                session,
                event_type="thread.resolved.reviewer",
                conversation_id=conversation_id,
                mailbox=mailbox,
                payload={
                    "draft_id": str(draft_id),
                    "thread_id": str(existing.thread_id),
                    "reason_code": reason_code,
                    "resolution_reason": "wrong_action",
                    "resolution_summary": RESOLUTION_SUMMARIES["wrong_action"],
                    "resolve_snapshot": snapshot,
                    "human": {
                        "title": "Marked resolved",
                        "body": RESOLUTION_SUMMARIES["wrong_action"],
                        "actor_kind": "elise",
                    },
                },
                actor=actor,
            )
        except Exception:
            logger.warning(
                "draft_feedback_audit_failed",
                draft_id=str(draft_id),
                event_type="thread.resolved.reviewer",
            )

    logger.info(
        "draft_marked_wrong",
        draft_id=str(draft_id),
        actor=actor,
        reason_code=reason_code,
    )
    return updated
