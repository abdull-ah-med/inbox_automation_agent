"""Manual resolve and resolution-feedback (reopen / wrong reason)."""

from __future__ import annotations

import uuid
from typing import Literal

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ThreadNotFoundError, ThreadStateError
from app.core.draftassistant_resolve import (
    REOPEN_ACTION_FINGERPRINT,
    RESOLUTION_SUMMARIES,
    build_resolve_snapshot,
    restore_disposition_from_snapshot,
)
from app.core.tenant_scope import TenantScope
from app.models.schemas.email import ThreadStateEnum
from app.repositories import audit_repo, message_repo, thread_repo
from app.services import audit_service
from app.services.workflow_extraction_service import maybe_extract_workflow

logger = structlog.get_logger(__name__)

ResolutionFeedbackAction = Literal["reopen", "wrong_reason"]


async def _require_thread(
    session: AsyncSession, settings: Settings, thread_id: uuid.UUID
) -> thread_repo.ThreadSchema:
    thread = await thread_repo.get_by_id(session, thread_id, TenantScope.from_settings(settings))
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise ThreadNotFoundError(f"Thread not found: {thread_id}")
    return thread


def _compose_resolve_learning_text(
    *,
    actions_taken: str,
    involved: str | None,
    subject: str | None,
    inbound_snippet: str | None,
) -> str:
    parts = [f"Actions taken: {actions_taken.strip()}"]
    if involved and involved.strip():
        parts.append(f"Involved: {involved.strip()}")
    if subject and subject.strip():
        parts.append(f"Subject: {subject.strip()}")
    if inbound_snippet and inbound_snippet.strip():
        parts.append(f"\nLatest inbound:\n{inbound_snippet.strip()}")
    return "\n".join(parts)


def _resolve_audit_body(
    *,
    resolved_actions: str,
    involved: str | None,
    already_resolved: bool,
) -> str:
    if already_resolved:
        body = "Recorded how you resolved this thread."
    else:
        body = "You closed this and took it off Needs Attention."
    body = f"{body} Actions taken: {resolved_actions}"
    if involved and involved.strip():
        body = f"{body} With: {involved.strip()}"
    return body


async def _record_resolve_capture(
    session: AsyncSession,
    settings: Settings,
    thread: thread_repo.ThreadSchema,
    thread_id: uuid.UUID,
    *,
    actor: str,
    resolved_actions: str,
    involved: str | None,
    already_resolved: bool,
    anthropic_client: AsyncAnthropic | None,
    openai_client: AsyncOpenAI | None,
) -> None:
    body = _resolve_audit_body(
        resolved_actions=resolved_actions,
        involved=involved,
        already_resolved=already_resolved,
    )
    snapshot = build_resolve_snapshot(
        resolved_by="elise",
        resolution_reason="manual",
        disposition_at_resolve="resolved_elise",
        had_draft=False,
        actions_taken=resolved_actions,
        involved=involved,
    )
    try:
        await audit_service.log_event(
            session,
            event_type="thread.resolved.reviewer",
            conversation_id=thread.conversation_id,
            mailbox=thread.mailbox,
            payload={
                "urgency_assessed": thread.urgency,
                "actions_taken": resolved_actions,
                "involved": involved,
                "already_resolved": already_resolved,
                "resolution_reason": "manual",
                "resolution_summary": RESOLUTION_SUMMARIES["manual"],
                "resolve_snapshot": snapshot,
                "human": {
                    "title": "You resolved this",
                    "body": body,
                    "actor_kind": "elise",
                },
            },
            actor=actor,
        )
    except Exception:
        logger.warning("manual_resolve_audit_failed", thread_id=str(thread_id))

    if anthropic_client is None:
        return
    try:
        inbound_snippet = await message_repo.latest_inbound_body_snippet(session, thread_id)
        learning_text = _compose_resolve_learning_text(
            actions_taken=resolved_actions,
            involved=involved,
            subject=thread.subject,
            inbound_snippet=inbound_snippet,
        )
        sender_addr = thread.alert_sender_norm or ""
        sender_dom = sender_addr.split("@")[-1] if "@" in sender_addr else ""
        await maybe_extract_workflow(
            session,
            client=anthropic_client,
            settings=settings,
            openai_client=openai_client,
            mailbox=thread.mailbox,
            thread_id=thread_id,
            email_text=learning_text,
            thread_summary=thread.subject or "",
            sender_address=sender_addr or None,
            sender_domain=sender_dom or None,
            routing_category=None,
            atomization_text=resolved_actions,
        )
    except Exception:
        logger.warning("workflow_extraction_resolve_failed", thread_id=str(thread_id))


async def resolve_thread_manual(
    session: AsyncSession,
    settings: Settings,
    thread_id: uuid.UUID,
    *,
    actor: str,
    actions_taken: str,
    involved: str | None = None,
    anthropic_client: AsyncAnthropic | None = None,
    openai_client: AsyncOpenAI | None = None,
) -> str:
    resolved_actions = actions_taken.strip()
    if not resolved_actions:
        raise ValueError("actions_taken is required")

    thread = await _require_thread(session, settings, thread_id)
    already_resolved = thread.state == ThreadStateEnum.RESOLVED.value
    if not already_resolved:
        await thread_repo.set_thread_outcome(
            session,
            thread_id,
            state=ThreadStateEnum.RESOLVED.value,
        )

    await _record_resolve_capture(
        session,
        settings,
        thread,
        thread_id,
        actor=actor,
        resolved_actions=resolved_actions,
        involved=involved,
        already_resolved=already_resolved,
        anthropic_client=anthropic_client,
        openai_client=openai_client,
    )

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
        snapshot = await audit_repo.get_latest_resolve_snapshot(
            session,
            mailbox=thread.mailbox,
            conversation_id=thread.conversation_id,
        )
        restore = restore_disposition_from_snapshot(snapshot)
        new_state = ThreadStateEnum.DRAFTED.value
        await thread_repo.set_thread_outcome(session, thread_id, state=new_state)
        if restore == "action_no_draft" and not thread.alert_fingerprint:
            await thread_repo.set_alert_fingerprint(
                session,
                thread_id,
                REOPEN_ACTION_FINGERPRINT,
            )
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
                    "restore_disposition": restore,
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
    return str(thread.state)
