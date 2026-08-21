"""Manual resolve and resolution-feedback (reopen / wrong reason)."""

from __future__ import annotations

import uuid
from typing import Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ThreadNotFoundError, ThreadStateError
from app.models.schemas.email import ThreadStateEnum
from app.repositories import thread_repo
from app.services import audit_service

logger = structlog.get_logger(__name__)

ResolutionFeedbackAction = Literal["reopen", "wrong_reason"]


async def _require_thread(session: AsyncSession, settings: Settings, thread_id: uuid.UUID):
    thread = await thread_repo.get_by_id(session, thread_id)
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise ThreadNotFoundError(f"Thread not found: {thread_id}")
    return thread


async def resolve_thread_manual(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
    *,
    actor: str,
    note: str | None = None,
) -> str:
    thread = await _require_thread(session, settings, thread_id)
    if thread.state == ThreadStateEnum.RESOLVED.value:
        return thread.state
    await thread_repo.set_thread_outcome(
        session,
        thread_id,
        state=ThreadStateEnum.RESOLVED.value,
    )
    body = "You marked this thread resolved. Removed from Needs Attention."
    if thread.urgency:
        body = (
            f"{body} Assessed urgency was {thread.urgency}; "
            "it no longer drives priority."
        )
    if note and note.strip():
        body = f"{body} Note: {note.strip()}"
    try:
        await audit_service.log_event(
            session,
            event_type="thread.resolved.reviewer",
            conversation_id=thread.conversation_id,
            mailbox=thread.mailbox,
            payload={
                "urgency_assessed": thread.urgency,
                "note": note,
                "human": {
                    "title": "Marked resolved",
                    "body": body,
                    "actor_kind": "elise",
                },
            },
            actor=actor,
        )
    except Exception:
        logger.warning("manual_resolve_audit_failed", thread_id=str(thread_id))
    return ThreadStateEnum.RESOLVED.value


async def apply_resolution_feedback(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
    *,
    action: ResolutionFeedbackAction,
    actor: str,
    note: str | None = None,
) -> str:
    thread = await _require_thread(session, settings, thread_id)
    if action == "reopen":
        if thread.state not in {
            ThreadStateEnum.RESOLVED.value,
            ThreadStateEnum.NO_ACTION.value,
        }:
            raise ThreadStateError("Thread is not finished; cannot reopen")
        new_state = ThreadStateEnum.DRAFTED.value
        await thread_repo.set_thread_outcome(session, thread_id, state=new_state)
        body = "Reopened after your feedback. Back in Needs Attention when a draft awaits review."
        if note and note.strip():
            body = f"{body} Note: {note.strip()}"
        try:
            await audit_service.log_event(
                session,
                event_type="thread.reopened.resolution_feedback",
                conversation_id=thread.conversation_id,
                mailbox=thread.mailbox,
                payload={
                    "previous_state": thread.state,
                    "note": note,
                    "human": {
                        "title": "Reopened after feedback",
                        "body": body,
                        "actor_kind": "elise",
                    },
                },
                actor=actor,
            )
        except Exception:
            logger.warning("reopen_audit_failed", thread_id=str(thread_id))
        return new_state

    # wrong_reason — keep resolved, record correction for learning
    body = "Thanks — we recorded that the auto-resolve reason was wrong."
    if note and note.strip():
        body = f"{body} Note: {note.strip()}"
    try:
        await audit_service.log_event(
            session,
            event_type="thread.resolved.wrong_reason",
            conversation_id=thread.conversation_id,
            mailbox=thread.mailbox,
            payload={
                "note": note,
                "human": {
                    "title": "Resolution reason corrected",
                    "body": body,
                    "actor_kind": "elise",
                },
            },
            actor=actor,
        )
    except Exception:
        logger.warning("wrong_reason_audit_failed", thread_id=str(thread_id))
    return thread.state
