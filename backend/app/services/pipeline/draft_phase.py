"""Draft-phase thread outcome persistence and audit payloads."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_scope import TenantScope
from app.llm.prompts import PROMPT_VERSION
from app.models.schemas.email import ThreadStateEnum
from app.models.schemas.email_triage_state import EmailTriageState
from app.repositories import thread_repo
from app.services.pipeline.triage_phase import _invalidate_chat_cache_threads

logger = structlog.get_logger(__name__)


async def _apply_draft_outcome_state(
    session: AsyncSession,
    *,
    state: EmailTriageState,
    thread_id: uuid.UUID | None,
) -> None:
    """Persist DRAFTED (+ the draft's urgency) or REQUIRES_HUMAN onto the thread.

    Only reached for messages that passed triage (``action_needed``) — the
    counterpart to ``_apply_triage_outcome_state`` for the draft step.
    """
    if thread_id is None:
        return
    if state.draft_status == "DRAFTED":
        urgency = state.draft.urgency if state.draft is not None else None
        urgency_reason = state.draft.urgency_reason if state.draft is not None else None
        new_state = ThreadStateEnum.DRAFTED.value
    elif state.draft_status == "REQUIRES_HUMAN":
        urgency = None
        urgency_reason = None
        new_state = ThreadStateEnum.REQUIRES_HUMAN.value
    else:
        return
    try:
        await thread_repo.set_thread_outcome(
            session,
            thread_id,
            state=new_state,
            urgency=urgency,
            urgency_reason=urgency_reason,
        )
        if state.draft_status in ("DRAFTED", "REQUIRES_HUMAN"):
            from app.services import recurrence_service, related_thread_service

            try:
                await related_thread_service.propose_alert_associations(
                    session, thread_id=thread_id
                )
            except Exception:
                logger.warning(
                    "alert_association_propose_failed",
                    thread_id=str(thread_id),
                )

            try:
                await related_thread_service.propose_drip_associations(session, thread_id=thread_id)
            except Exception:
                logger.warning(
                    "drip_association_propose_failed",
                    thread_id=str(thread_id),
                )

            applied = None
            try:
                applied = await recurrence_service.apply_recurrence_escalation(
                    session,
                    thread_id=thread_id,
                    conversation_id=state.original_email.conversation_id,
                    mailbox=state.original_email.mailbox,
                    assessed_urgency=urgency,
                )
            except Exception:
                logger.warning(
                    "recurrence_escalation_failed",
                    thread_id=str(thread_id),
                )
            if (
                state.draft_status == "DRAFTED"
                and state.draft is not None
                and applied
                and applied != urgency
            ):
                thread_row = await thread_repo.get_by_id(
                    session,
                    thread_id,
                    TenantScope.single(state.original_email.mailbox),
                )
                bump_reason = (
                    thread_row.urgency_reason
                    if thread_row is not None and thread_row.urgency_reason
                    else (
                        f"{urgency_reason}; recurrence floor {applied}"
                        if urgency_reason
                        else f"recurrence floor {applied}"
                    )
                )
                state.draft = state.draft.model_copy(
                    update={
                        "urgency": applied,
                        "urgency_reason": bump_reason,
                    }
                )
        await _invalidate_chat_cache_threads(session, [thread_id])
    except Exception:
        logger.exception(
            "thread_state_update_failed",
            thread_id=str(thread_id),
            conversation_id=state.original_email.conversation_id,
            target_state=new_state,
        )


def _draft_audit_payload(state: EmailTriageState) -> dict[str, object]:
    payload: dict[str, object] = {
        "draft_status": state.draft_status,
        "prompt_version": PROMPT_VERSION,
    }
    if state.draft is not None:
        payload["urgency"] = state.draft.urgency
    if state.error_logs:
        payload["error_logs"] = list(state.error_logs)
    return payload
