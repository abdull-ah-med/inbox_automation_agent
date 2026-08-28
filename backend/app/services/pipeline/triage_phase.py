"""Triage-phase outcome persistence and audit payloads."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_mail import is_internal_sender
from app.llm.prompts import PROMPT_VERSION
from app.models.schemas.audit_events import TriageAuditEvent
from app.models.schemas.email import ThreadStateEnum
from app.models.schemas.email_triage_state import EmailTriageState
from app.repositories import chat_cache_repo, spam_allowlist_repo, thread_repo
from app.services import audit_service
from app.services.triage_service import decide_triage_outcome

logger = structlog.get_logger(__name__)


async def _invalidate_chat_cache_mailbox(session: AsyncSession, mailbox: str | None) -> None:
    if not mailbox:
        return
    try:
        await chat_cache_repo.invalidate_for_mailbox(session, mailbox.strip().lower())
    except Exception:
        logger.warning("chat_cache_invalidate_mailbox_failed", mailbox=mailbox)


async def _invalidate_chat_cache_threads(
    session: AsyncSession,
    thread_ids: list[uuid.UUID],
) -> None:
    if not thread_ids:
        return
    try:
        await chat_cache_repo.invalidate_for_threads(session, thread_ids)
    except Exception:
        logger.warning(
            "chat_cache_invalidate_threads_failed",
            thread_ids=[str(item) for item in thread_ids],
        )


async def _allowlisted_senders(session: object, mailbox: str) -> frozenset[str]:
    """Load reviewer-corrected senders. No-op when ``session`` is not a DB session."""
    if not isinstance(session, AsyncSession):
        return frozenset()
    return await spam_allowlist_repo.addresses_for_mailbox(session, mailbox)


_OUTCOME_EVENT_TYPES = {
    "spam_discarded": TriageAuditEvent.SPAM_DISCARDED,
    "no_action_discarded": TriageAuditEvent.NO_ACTION_DISCARDED,
    "action_needed": TriageAuditEvent.ACTION_NEEDED,
}

# Terminal triage outcomes that write straight to ``threads.state`` — the
# ``action_needed`` outcome intentionally has no entry here: the thread only
# moves to DRAFTED/REQUIRES_HUMAN once the draft step resolves further down.
_OUTCOME_THREAD_STATE = {
    "spam_discarded": ThreadStateEnum.SPAM.value,
}


async def _apply_triage_outcome_state(
    session: AsyncSession,
    *,
    state: EmailTriageState,
    thread_id: uuid.UUID | None,
    apply_thread_state: bool = True,
) -> None:
    """Persist SPAM/NO_ACTION onto the thread so filtered views can exclude it.

    Best-effort: a thread lookup miss (e.g. race with a concurrent delete)
    must never fail the pipeline — triage/audit already succeeded.

    When ``apply_thread_state`` is False (human already replied / RESOLVED),
    skip writing SPAM/NO_ACTION so the human resolve outcome wins.
    """
    if state.triage is None or thread_id is None:
        return
    outcome, _ = decide_triage_outcome(state.triage)
    new_state = _OUTCOME_THREAD_STATE.get(outcome)
    if new_state is None:
        return
    if not apply_thread_state:
        return
    try:
        await thread_repo.set_thread_outcome(session, thread_id, state=new_state)
        await _invalidate_chat_cache_threads(session, [thread_id])
        if outcome == "no_action_discarded":
            from app.core.closing_mail import looks_like_closing_mail

            body = state.original_email.body_clean or state.original_email.body_text
            if looks_like_closing_mail(body):
                try:
                    await audit_service.log_event(
                        session,
                        event_type="thread.outcome.closing_inbound",
                        conversation_id=state.original_email.conversation_id,
                        mailbox=state.original_email.mailbox,
                        payload={
                            "human": {
                                "title": "Closed — courtesy inbound",
                                "body": (
                                    "Detected a closing / courtesy message with no open ask. "
                                    "Marked no action and removed from Needs Attention."
                                ),
                                "actor_kind": "agent",
                            }
                        },
                        actor="system",
                    )
                except Exception:
                    logger.warning(
                        "closing_inbound_audit_failed",
                        thread_id=str(thread_id),
                    )
    except Exception:
        logger.exception(
            "thread_state_update_failed",
            thread_id=str(thread_id),
            conversation_id=state.original_email.conversation_id,
            target_state=new_state,
        )


def _audit_event_type(state: EmailTriageState) -> str:
    if state.triage is None:
        return TriageAuditEvent.FAILED
    outcome, _ = decide_triage_outcome(state.triage)
    return _OUTCOME_EVENT_TYPES[outcome]


def _audit_payload(state: EmailTriageState) -> dict[str, object]:
    """Metadata-only audit fields — no LLM free-text that may echo email PII."""
    triage = state.triage
    payload: dict[str, object] = {
        "draft_status": state.draft_status,
        "prompt_version": PROMPT_VERSION,
    }
    if triage is not None:
        payload.update(
            {
                "is_spam": triage.is_spam,
                "has_action_items": triage.has_action_items,
                "draft_needed": triage.draft_needed,
                "needs_context": triage.needs_context,
                "routing_category": triage.routing_category,
                "is_internal": is_internal_sender(
                    state.original_email.sender,
                    state.original_email.mailbox,
                ),
                "is_automated": bool(triage.is_automated),
            }
        )
    if state.error_logs:
        payload["error_logs"] = list(state.error_logs)
    return payload
