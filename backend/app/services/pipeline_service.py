"""Post-ingest pipeline — triage, then Sonnet draft when action is needed."""

from __future__ import annotations

import uuid

import structlog
from anthropic import AsyncAnthropic
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AuditError
from app.llm.prompts import PROMPT_VERSION
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import EmailTriageState
from app.models.schemas.graph import IngestResultSchema
from app.services import audit_service, draft_service, triage_service
from app.services.triage_service import decide_triage_outcome

logger = structlog.get_logger(__name__)

_OUTCOME_EVENT_TYPES = {
    "spam_discarded": "triage.spam_discarded",
    "no_action_discarded": "triage.no_action_discarded",
    "action_needed": "triage.action_needed",
}


def _select_original_email(
    *,
    message_id: str,
    thread_context: ThreadContextSchema,
) -> EmailMessageSchema:
    for msg in thread_context.messages:
        if msg.message_id == message_id:
            return msg
    raise ValueError(
        f"message_id={message_id} not found in thread_context "
        f"({len(thread_context.messages)} messages)"
    )


def _audit_event_type(state: EmailTriageState) -> str:
    if state.triage is None:
        return "triage.failed"
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
                "needs_context": triage.needs_context,
            }
        )
    if state.error_logs:
        payload["error_logs"] = list(state.error_logs)
    return payload


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


_TRIAGE_ELIGIBLE_STATUSES = frozenset({"ingested", "retry_triage"})


async def run_after_ingest(
    *,
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    client: AsyncAnthropic,
    ingest_result: IngestResultSchema,
    original_email: EmailMessageSchema | None = None,
    thread_context: ThreadContextSchema | None = None,
) -> EmailTriageState:
    """Build ``EmailTriageState``, run triage (+ draft when PENDING), and audit.

    ``redis`` is accepted for call-site symmetry with ingest; no new Redis keys
    are written in this stage (dedup already completed by the caller).
    """
    del redis

    if ingest_result.status not in _TRIAGE_ELIGIBLE_STATUSES:
        raise ValueError(
            f"run_after_ingest requires status in "
            f"{sorted(_TRIAGE_ELIGIBLE_STATUSES)}, got {ingest_result.status!r}"
        )

    context = thread_context or ingest_result.thread_context
    if context is None:
        raise ValueError("thread_context is required for post-ingest triage")

    email = original_email or _select_original_email(
        message_id=ingest_result.message_id,
        thread_context=context,
    )

    state = EmailTriageState(
        original_email=email,
        thread_context=context,
        draft_status="PENDING",
    )
    state = await triage_service.run_triage(state, client=client, settings=settings)

    event_type = _audit_event_type(state)
    try:
        await audit_service.log_event(
            session,
            event_type=event_type,
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            payload=_audit_payload(state),
            actor="system",
        )
    except AuditError:
        logger.exception(
            "triage_audit_failed",
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            event_type=event_type,
        )

    if state.draft_status != "PENDING":
        return state

    if not ingest_result.thread_id:
        state.draft_status = "REQUIRES_HUMAN"
        state.error_logs.append("draft_skipped:missing_thread_id")
        try:
            await audit_service.log_event(
                session,
                event_type="draft.requires_human",
                conversation_id=email.conversation_id,
                mailbox=email.mailbox,
                payload=_draft_audit_payload(state),
                actor="system",
            )
        except AuditError:
            logger.exception(
                "draft_audit_failed",
                conversation_id=email.conversation_id,
                mailbox=email.mailbox,
                event_type="draft.requires_human",
            )
        return state

    state = await draft_service.run_draft(
        state,
        session=session,
        client=client,
        settings=settings,
        thread_id=uuid.UUID(ingest_result.thread_id),
    )

    draft_event = "draft.generated" if state.draft_status == "DRAFTED" else "draft.requires_human"
    try:
        await audit_service.log_event(
            session,
            event_type=draft_event,
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            payload=_draft_audit_payload(state),
            actor="system",
        )
    except AuditError:
        logger.exception(
            "draft_audit_failed",
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            event_type=draft_event,
        )

    return state


async def run_post_ingest_triage(
    *,
    redis: Redis,
    settings: Settings,
    ingest_result: IngestResultSchema,
) -> EmailTriageState | None:
    """Open a fresh DB session and run triage+draft+audit after a successful ingest commit.

    Used by webhook/poll background paths. Returns None when status is ineligible,
    triage failed (``state.triage is None``), or an unexpected error occurs.
    Failures are logged and not raised (ingest already succeeded).
    """
    if ingest_result.status not in _TRIAGE_ELIGIBLE_STATUSES:
        return None

    from app.core.dependencies import anthropic_client_from_settings
    from app.db.session import get_session_factory

    client = anthropic_client_from_settings(settings)
    session_factory = get_session_factory()
    try:
        async with session_factory() as session, session.begin():
            state = await run_after_ingest(
                session=session,
                redis=redis,
                settings=settings,
                client=client,
                ingest_result=ingest_result,
            )
        if state.triage is None:
            return None
        return state
    except Exception:
        logger.exception(
            "post_ingest_triage_failed",
            message_id=ingest_result.message_id,
            conversation_id=ingest_result.conversation_id,
        )
        return None
