"""Thread lifecycle side effects during pipeline triage (keeps service.py under LOC budget)."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.draftassistant_resolve import (
    build_resolve_snapshot,
    draftassistant_auto_close_decision,
    should_reopen_finished_thread,
)
from app.models.schemas.email import ThreadStateEnum
from app.models.schemas.email_triage_state import EmailTriageState
from app.repositories import thread_repo

logger = structlog.get_logger(__name__)


async def reopen_finished_if_action_needed(
    session: AsyncSession,
    *,
    state: EmailTriageState,
    thread_id: uuid.UUID | None,
) -> None:
    if thread_id is None or state.triage is None:
        return
    try:
        prior = await thread_repo.get_by_id_trusted(session, thread_id)
        if prior is None:
            return
        if not should_reopen_finished_thread(
            prior_state=prior.state,
            has_action_items=state.triage.has_action_items,
        ):
            return
        await thread_repo.set_thread_outcome(
            session,
            thread_id,
            state=ThreadStateEnum.DRAFTED.value,
        )
        from app.services.pipeline.service import _safe_audit

        await _safe_audit(
            session,
            state=state,
            event_type="thread.reopened.inbound_followup",
            payload={
                "prior_state": prior.state,
                "message_id": state.original_email.message_id,
                "human": {
                    "title": "Reopened after new inbound",
                    "body": "New inbound has action items. Thread is open again.",
                    "actor_kind": "agent",
                },
            },
        )
    except Exception:
        logger.warning("reopen_finished_lookup_failed", thread_id=str(thread_id))


async def apply_draftassistant_auto_resolve(
    session: AsyncSession,
    *,
    state: EmailTriageState,
    thread_id: uuid.UUID | None,
    settings: Settings,
) -> EmailTriageState | None:
    if thread_id is None:
        return None
    owner = settings.owner_for_mailbox(state.original_email.mailbox)
    decision = draftassistant_auto_close_decision(state, owner_name=owner)
    if decision is None:
        return None
    await thread_repo.set_thread_outcome(
        session,
        thread_id,
        state=ThreadStateEnum.RESOLVED.value,
    )
    snapshot = build_resolve_snapshot(
        resolved_by="draftassistant",
        resolution_reason=decision.reason,
        disposition_at_resolve="resolved_draftassistant",
        had_draft=False,
        has_action_items=False,
        draft_needed=False,
        confidence_tier=decision.confidence_tier,
    )
    from app.services.pipeline.service import _safe_audit

    await _safe_audit(
        session,
        state=state,
        event_type="thread.resolved.draftassistant",
        payload={
            "resolution_reason": decision.reason,
            "resolution_summary": decision.summary,
            "resolve_snapshot": snapshot,
            "human": {
                "title": "Resolved by DraftAssistant",
                "body": decision.summary,
                "actor_kind": "agent",
            },
        },
    )
    state.draft_status = "SKIPPED"
    state.slack_delivery = "not_required"
    return state
