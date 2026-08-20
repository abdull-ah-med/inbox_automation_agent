"""Post-ingest pipeline — triage, then Sonnet draft when action is needed."""

from __future__ import annotations

import asyncio
import uuid

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import AuditError, DraftGenerationError
from app.core.automated_mail import is_automated_mail
from app.core.internal_mail import is_internal_sender
from app.graph.client import GraphClient
from app.llm import draft_generator as draft_llm
from app.llm.prompts import PROMPT_VERSION
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema, ThreadStateEnum
from app.models.schemas.email_triage_state import CrossThreadContextSchema, EmailTriageState
from app.models.schemas.graph import IngestResultSchema
from app.repositories import chat_cache_repo, draft_repo, skill_repo, spam_allowlist_repo, thread_repo
from app.services import (
    audit_service,
    context_service,
    draft_service,
    embedding_service,
    rejection_memory_service,
    reply_memory_service,
    skill_selection_service,
    slack_service,
    summary_service,
    tone_profile_service,
    triage_service,
    urgency_feedback_service,
)
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
    "spam_discarded": "triage.spam_discarded",
    "no_action_discarded": "triage.no_action_discarded",
    "action_needed": "triage.action_needed",
}

# Terminal triage outcomes that write straight to ``threads.state`` — the
# ``action_needed`` outcome intentionally has no entry here: the thread only
# moves to DRAFTED/REQUIRES_HUMAN once the draft step resolves further down.
_OUTCOME_THREAD_STATE = {
    "spam_discarded": ThreadStateEnum.SPAM.value,
    "no_action_discarded": ThreadStateEnum.NO_ACTION.value,
}


async def _apply_triage_outcome_state(
    session: AsyncSession,
    *,
    state: EmailTriageState,
    thread_id: uuid.UUID | None,
) -> None:
    """Persist SPAM/NO_ACTION onto the thread so filtered views can exclude it.

    Best-effort: a thread lookup miss (e.g. race with a concurrent delete)
    must never fail the pipeline — triage/audit already succeeded.
    """
    if state.triage is None or thread_id is None:
        return
    outcome, _ = decide_triage_outcome(state.triage)
    new_state = _OUTCOME_THREAD_STATE.get(outcome)
    if new_state is None:
        return
    try:
        await thread_repo.set_thread_outcome(session, thread_id, state=new_state)
        await _invalidate_chat_cache_threads(session, [thread_id])
    except Exception:
        logger.exception(
            "thread_state_update_failed",
            thread_id=str(thread_id),
            conversation_id=state.original_email.conversation_id,
            target_state=new_state,
        )


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
        await _invalidate_chat_cache_threads(session, [thread_id])
    except Exception:
        logger.exception(
            "thread_state_update_failed",
            thread_id=str(thread_id),
            conversation_id=state.original_email.conversation_id,
            target_state=new_state,
        )


# Spam / no-action skips are always terminal for dedup.
# DRAFTED is terminal only when Slack delivery is in this set.
_SLACK_OK_FOR_DEDUP = frozenset(
    {"posted", "already_posted", "skipped_unconfigured", "not_required"}
)


def pipeline_ready_for_dedup(state: EmailTriageState) -> bool:
    """True when webhook/poll may mark ingest dedup completed.

    ``REQUIRES_HUMAN`` (draft/triage failure) must NOT complete dedup so poll
    can retry. Spam / no-action skips are terminal. Successful drafts complete
    only when Slack delivery is OK (posted, already posted, intentionally
    unconfigured, or not required on the simulate path). Slack post failure
    must leave dedup open for retry.
    """
    if state.triage is None:
        return False
    if state.draft_status == "SKIPPED":
        return True
    if state.draft_status == "DRAFTED":
        return state.slack_delivery in _SLACK_OK_FOR_DEDUP
    return False


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
                "routing_category": triage.routing_category,
                "is_internal": is_internal_sender(
                    state.original_email.sender,
                    state.original_email.mailbox,
                ),
                "is_automated": is_automated_mail(
                    sender=state.original_email.sender,
                    subject=state.original_email.subject,
                ),
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


def _context_audit_payload(
    cross: CrossThreadContextSchema | None,
) -> dict[str, object]:
    if cross is None:
        return {}
    return {
        "similarity_score": cross.similarity_score,
        "matched_conversation_id": cross.matched_conversation_id,
    }


async def _resolve_graph_client(
    settings: Settings,
    graph_client: GraphClient | None,
) -> GraphClient | None:
    """Return an injected client or build one; ``None`` when Graph is unconfigured.

    Must ``await`` ``get_graph_auth`` (async) and pass Redis — never call the
    coroutine synchronously (that would poison the process-scoped GraphClient
    singleton with a coroutine object).
    """
    if graph_client is not None:
        return graph_client
    try:
        from app.core.dependencies import get_graph_auth, get_graph_client, get_redis

        redis = await get_redis()
        auth = await get_graph_auth(settings, redis)
        return get_graph_client(auth)
    except Exception:
        logger.warning(
            "cross_thread_graph_client_unavailable",
            environment=settings.environment,
            exc_info=True,
        )
        return None


_TRIAGE_ELIGIBLE_STATUSES = frozenset({"ingested", "retry_triage"})


async def _summarize_non_spam(
    *,
    state: EmailTriageState,
    client: AsyncAnthropic,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Summarize current + siblings when triage is non-spam; never raises."""
    if state.triage is None or state.triage.is_spam:
        return
    try:
        async with session_factory() as session, session.begin():
            await summary_service.summarize_current_if_needed(
                session,
                email=state.original_email,
                client=client,
                settings=settings,
            )
        await summary_service.backfill_sibling_summaries(
            session_factory=session_factory,
            emails=list(state.thread_context.messages),
            current_message_id=state.original_email.message_id,
            client=client,
            settings=settings,
        )
        # Refresh summary fields on thread_context messages from originals we mutated.
        by_id = {m.message_id: m for m in state.thread_context.messages}
        if state.original_email.message_id in by_id:
            by_id[
                state.original_email.message_id
            ].summary_one_line = state.original_email.summary_one_line
            by_id[state.original_email.message_id].summary_json = state.original_email.summary_json
    except Exception:
        logger.exception(
            "pipeline_summary_failed",
            conversation_id=state.original_email.conversation_id,
            message_id=state.original_email.message_id,
        )


async def _refresh_thread_summary_safe(
    *,
    thread_id: uuid.UUID,
    client: AsyncAnthropic,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.services import thread_summary_service

    try:
        async with session_factory() as session, session.begin():
            await thread_summary_service.maybe_refresh_safe(
                session,
                thread_id=thread_id,
                client=client,
                settings=settings,
            )
    except Exception:
        logger.exception("pipeline_thread_summary_failed", thread_id=str(thread_id))


async def _safe_audit(
    session: AsyncSession,
    *,
    state: EmailTriageState,
    event_type: str,
    payload: dict[str, object],
) -> None:
    email = state.original_email
    try:
        await audit_service.log_event(
            session,
            event_type=event_type,
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            payload=payload,
            actor="system",
        )
    except AuditError:
        logger.exception(
            "pipeline_audit_failed",
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            event_type=event_type,
        )


def _should_embed(state: EmailTriageState, openai_client: AsyncOpenAI | None) -> bool:
    """Embed every non-spam email that reached a triage result — only when OpenAI is configured."""
    if openai_client is None:
        return False
    return state.triage is not None and not state.triage.is_spam


async def _embed_non_spam_safe(
    session: AsyncSession,
    *,
    state: EmailTriageState,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
) -> None:
    if not _should_embed(state, openai_client):
        return
    assert openai_client is not None  # narrowed by _should_embed
    await embedding_service.embed_and_store_safe(
        session,
        email=state.original_email,
        client=openai_client,
        settings=settings,
    )


async def _embed_in_fresh_session(
    *,
    state: EmailTriageState,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Embed in its own short transaction (safe to gather with draft work)."""
    if not _should_embed(state, openai_client):
        return
    assert openai_client is not None  # narrowed by _should_embed
    async with session_factory() as session, session.begin():
        await embedding_service.embed_and_store_safe(
            session,
            email=state.original_email,
            client=openai_client,
            settings=settings,
        )


async def _post_slack_card_after_commit(
    state: EmailTriageState,
    *,
    redis: Redis,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> EmailTriageState:
    """Post Slack only after draft/audit rows are committed; audit in a fresh txn.

    Updates ``state.slack_delivery`` so callers can decide ingest dedup.
    """
    from app.core.dependencies import slack_app_from_settings

    slack_app = slack_app_from_settings(settings)
    email = state.original_email
    result = await slack_service.post_review_card(
        state,
        redis=redis,
        slack_app=slack_app,
        settings=settings,
    )
    state.slack_delivery = result.slack_delivery

    if result.status == "failed":
        logger.warning(
            "slack_card_post_failed",
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            message_id=email.message_id,
        )
        return state

    if result.message_ts is None:
        return state

    async with session_factory() as session, session.begin():
        await _safe_audit(
            session,
            state=state,
            event_type="slack.card_posted",
            payload={
                "message_id": email.message_id,
                "message_ts": result.message_ts,
                "draft_status": state.draft_status,
            },
        )
    return state


async def run_after_ingest(
    *,
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    client: AsyncAnthropic,
    ingest_result: IngestResultSchema,
    original_email: EmailMessageSchema | None = None,
    thread_context: ThreadContextSchema | None = None,
    openai_client: AsyncOpenAI | None = None,
    graph_client: GraphClient | None = None,
) -> EmailTriageState:
    """Build ``EmailTriageState``, run triage (+ draft when PENDING), and audit.

    Does **not** post Slack — callers that need a review card must call
    ``_post_slack_card_after_commit`` after the session commits (see
    ``run_post_ingest_triage``).

    Prefer ``run_phased_after_ingest`` for production/simulate paths so LLM and
    Graph I/O never run inside the caller's open transaction. This helper is
    kept for unit/live tests that inject a single session for audit/draft.
    Flow B store/search/link uses a separate short-lived session factory.
    """
    _ = redis
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
    allowlisted = await _allowlisted_senders(session, email.mailbox)
    state = await triage_service.run_triage(
        state,
        client=client,
        settings=settings,
        allowlisted_senders=allowlisted,
    )
    parsed_thread_id = uuid.UUID(ingest_result.thread_id) if ingest_result.thread_id else None
    await _invalidate_chat_cache_mailbox(session, email.mailbox)

    await _safe_audit(
        session,
        state=state,
        event_type=_audit_event_type(state),
        payload=_audit_payload(state),
    )
    await _apply_triage_outcome_state(session, state=state, thread_id=parsed_thread_id)

    from app.core.dependencies import openai_client_from_settings
    from app.db.session import get_session_factory

    embed_client = openai_client or openai_client_from_settings(settings)
    factory = get_session_factory()
    if state.triage is not None and not state.triage.is_spam:
        await _summarize_non_spam(
            state=state,
            client=client,
            settings=settings,
            session_factory=factory,
        )
        if parsed_thread_id is not None:
            await _refresh_thread_summary_safe(
                thread_id=parsed_thread_id,
                client=client,
                settings=settings,
                session_factory=factory,
            )
    needs_context = bool(state.triage and state.triage.needs_context)

    # Non-spam: always embed. Flow B (needs_context) embeds inside context_service.
    # Flow A embeds here before draft (shared AsyncSession is not safe across gather).
    if state.draft_status != "PENDING":
        await _embed_non_spam_safe(
            session,
            state=state,
            openai_client=embed_client,
            settings=settings,
        )
        if state.draft_status in {"SKIPPED", "DRAFTED"}:
            state.slack_delivery = "not_required"
        return state

    cross_thread: CrossThreadContextSchema | None = None
    if needs_context and embed_client is not None:
        # Only stand up a Graph client when embedding is actually configured —
        # cross-thread search cannot run without OpenAI, so there's no point
        # authenticating Graph just to throw the result away.
        resolved_graph = await _resolve_graph_client(settings, graph_client)
        if resolved_graph is not None:
            cross_thread = await context_service.resolve_cross_thread_context(
                state,
                session_factory=get_session_factory(),
                graph_client=resolved_graph,
                openai_client=embed_client,
                settings=settings,
            )
        state.cross_thread_context = cross_thread
        await _safe_audit(
            session,
            state=state,
            event_type="context.match" if cross_thread is not None else "context.no_match",
            payload=_context_audit_payload(cross_thread),
        )
        # Ensure Day-4 embed even when Flow B failed before store (or was skipped, unconfigured).
        if cross_thread is None:
            await _embed_non_spam_safe(
                session,
                state=state,
                openai_client=embed_client,
                settings=settings,
            )
    elif needs_context:
        logger.info(
            "cross_thread_context_skipped_no_openai_key",
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            message_id=email.message_id,
        )
    else:
        await _embed_non_spam_safe(
            session,
            state=state,
            openai_client=embed_client,
            settings=settings,
        )

    if not ingest_result.thread_id:
        state.draft_status = "REQUIRES_HUMAN"
        state.error_logs.append("draft_skipped:missing_thread_id")
        await _safe_audit(
            session,
            state=state,
            event_type="draft.requires_human",
            payload=_draft_audit_payload(state),
        )
        return state

    tone_references = await reply_memory_service.find_similar_replies(
        session,
        openai_client=embed_client,
        settings=settings,
        email_text=f"{email.subject}\n\n{email.body_clean or email.body_text}",
        mailbox=email.mailbox,
        limit=3,
    )

    state = await draft_service.run_draft(
        state,
        session=session,
        client=client,
        settings=settings,
        thread_id=uuid.UUID(ingest_result.thread_id),
        cross_thread_context=cross_thread,
        tone_references=tone_references,
        openai_client=embed_client,
    )

    draft_event = "draft.generated" if state.draft_status == "DRAFTED" else "draft.requires_human"
    await _safe_audit(
        session,
        state=state,
        event_type=draft_event,
        payload=_draft_audit_payload(state),
    )
    await _apply_draft_outcome_state(session, state=state, thread_id=parsed_thread_id)
    if state.draft_status == "DRAFTED":
        # Simulate / in-session path does not post Slack.
        state.slack_delivery = "not_required"

    return state


async def run_phased_after_ingest(
    *,
    redis: Redis,
    settings: Settings,
    ingest_result: IngestResultSchema,
    client: AsyncAnthropic | None = None,
    openai_client: AsyncOpenAI | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    graph_client: GraphClient | None = None,
    post_slack: bool = True,
) -> EmailTriageState:
    """Triage/draft LLMs and Graph I/O outside DB transactions; short write txns.

    Prefer this over ``run_after_ingest`` for webhook, poll, and simulate so
    Postgres never sits idle-in-transaction during network calls
    (https://www.postgresql.org/docs/current/runtime-config-client.html).
    """
    from app.core.dependencies import anthropic_client_from_settings, openai_client_from_settings
    from app.db.session import get_session_factory

    if ingest_result.status not in _TRIAGE_ELIGIBLE_STATUSES:
        raise ValueError(
            f"run_phased_after_ingest requires status in "
            f"{sorted(_TRIAGE_ELIGIBLE_STATUSES)}, got {ingest_result.status!r}"
        )

    resolved_client = client or anthropic_client_from_settings(settings)
    resolved_openai = openai_client or openai_client_from_settings(settings)
    resolved_factory = session_factory or get_session_factory()
    return await _run_phased_post_ingest(
        redis=redis,
        settings=settings,
        client=resolved_client,
        openai_client=resolved_openai,
        ingest_result=ingest_result,
        session_factory=resolved_factory,
        graph_client=graph_client,
        post_slack=post_slack,
    )


async def _run_phased_post_ingest(
    *,
    redis: Redis,
    settings: Settings,
    client: AsyncAnthropic,
    openai_client: AsyncOpenAI | None,
    ingest_result: IngestResultSchema,
    session_factory: async_sessionmaker[AsyncSession],
    graph_client: GraphClient | None = None,
    post_slack: bool = True,
) -> EmailTriageState:
    """Triage/draft LLMs outside DB transactions; short txns for writes; Slack after commit."""
    context = ingest_result.thread_context
    if context is None:
        raise ValueError("thread_context is required for post-ingest triage")

    email = _select_original_email(
        message_id=ingest_result.message_id,
        thread_context=context,
    )
    state = EmailTriageState(
        original_email=email,
        thread_context=context,
        draft_status="PENDING",
    )

    async with session_factory() as session:
        allowlisted = await _allowlisted_senders(session, email.mailbox)

    # --- LLM: triage (no DB connection held) ---
    state = await triage_service.run_triage(
        state,
        client=client,
        settings=settings,
        allowlisted_senders=allowlisted,
    )
    parsed_thread_id = uuid.UUID(ingest_result.thread_id) if ingest_result.thread_id else None

    async with session_factory() as session, session.begin():
        await _invalidate_chat_cache_mailbox(session, email.mailbox)
        await _safe_audit(
            session,
            state=state,
            event_type=_audit_event_type(state),
            payload=_audit_payload(state),
        )
        await _apply_triage_outcome_state(session, state=state, thread_id=parsed_thread_id)

    # Non-spam: summarize before draft/embed so packing has summary lines.
    if state.triage is not None and not state.triage.is_spam:
        await _summarize_non_spam(
            state=state,
            client=client,
            settings=settings,
            session_factory=session_factory,
        )
        if parsed_thread_id is not None:
            await _refresh_thread_summary_safe(
                thread_id=parsed_thread_id,
                client=client,
                settings=settings,
                session_factory=session_factory,
            )

    if state.draft_status != "PENDING":
        await _embed_in_fresh_session(
            state=state,
            openai_client=openai_client,
            settings=settings,
            session_factory=session_factory,
        )
        if state.draft_status == "SKIPPED":
            state.slack_delivery = "not_required"
        return state

    if not ingest_result.thread_id:
        state.draft_status = "REQUIRES_HUMAN"
        state.error_logs.append("draft_skipped:missing_thread_id")
        async with session_factory() as session, session.begin():
            await _safe_audit(
                session,
                state=state,
                event_type="draft.requires_human",
                payload=_draft_audit_payload(state),
            )
        await _embed_in_fresh_session(
            state=state,
            openai_client=openai_client,
            settings=settings,
            session_factory=session_factory,
        )
        return state

    thread_id = uuid.UUID(ingest_result.thread_id)
    message_id = email.message_id
    needs_context = bool(state.triage and state.triage.needs_context)

    cross_thread: CrossThreadContextSchema | None = None
    if needs_context and openai_client is not None:
        # OpenAI + Graph run inside context_service without holding a DB txn.
        # Only stand up a Graph client when embedding is actually configured —
        # cross-thread search cannot run without OpenAI, so there's no point
        # authenticating Graph just to throw the result away.
        resolved_graph = await _resolve_graph_client(settings, graph_client)
        if resolved_graph is not None:
            cross_thread = await context_service.resolve_cross_thread_context(
                state,
                session_factory=session_factory,
                graph_client=resolved_graph,
                openai_client=openai_client,
                settings=settings,
            )
        state.cross_thread_context = cross_thread
        async with session_factory() as session, session.begin():
            await _safe_audit(
                session,
                state=state,
                event_type=("context.match" if cross_thread is not None else "context.no_match"),
                payload=_context_audit_payload(cross_thread),
            )
        if cross_thread is None:
            await _embed_in_fresh_session(
                state=state,
                openai_client=openai_client,
                settings=settings,
                session_factory=session_factory,
            )
    elif needs_context:
        logger.info(
            "cross_thread_context_skipped_no_openai_key",
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            message_id=message_id,
        )

    # --- Short DB reads, then release before OpenAI / Haiku ---
    skill_contents: list[str] = []
    skill_ids: list[uuid.UUID] = []
    applied_skills: list = []
    tone_references: list[str] = []
    tone_profile_block: str | None = None
    negative_constraints: list[str] = []
    urgency_hints: list[str] = []
    email_text = f"{email.subject}\n\n{email.body_clean or email.body_text}"
    routing_category = state.triage.routing_category if state.triage is not None else "general"

    async with session_factory() as session:
        existing = await draft_repo.get_draft_by_message(session, message_id=message_id)
        active_skills: list = []
        if state.triage is not None:
            try:
                active_skills = await skill_repo.list_active_for_selection(session)
            except Exception:
                logger.exception(
                    "skill_load_failed",
                    conversation_id=email.conversation_id,
                    mailbox=email.mailbox,
                    message_id=message_id,
                )
                active_skills = []
        await session.commit()

    if state.triage is not None:
        try:
            selected = await skill_selection_service.select_from_active(
                active_skills,
                client=client,
                settings=settings,
                openai_client=openai_client,
                email=email,
                triage=state.triage,
            )
            skill_contents = selected.blocks
            skill_ids = selected.skill_ids
            applied_skills = list(selected.applied)
            async with session_factory() as session:
                await skill_selection_service.log_skills_selected(
                    session,
                    email=email,
                    conversation_id=email.conversation_id,
                    selected=selected,
                )
                await session.commit()
        except Exception:
            logger.exception(
                "skill_load_failed",
                conversation_id=email.conversation_id,
                mailbox=email.mailbox,
                message_id=message_id,
            )
            skill_contents = []
            skill_ids = []
            applied_skills = []

    async with session_factory() as session:
        tone_profile_block, tone_references = await tone_profile_service.load_for_draft(
            session,
            openai_client=openai_client,
            settings=settings,
            mailbox=email.mailbox,
            routing_category=routing_category,
            email_text=email_text,
        )
        negative_constraints = await rejection_memory_service.find_negative_constraints(
            session,
            openai_client=openai_client,
            settings=settings,
            email_text=email_text,
            mailbox=email.mailbox,
            routing_category=routing_category,
            limit=3,
        )
        urgency_hints = await urgency_feedback_service.find_urgency_hints(
            session,
            openai_client=openai_client,
            settings=settings,
            email_text=email_text,
            mailbox=email.mailbox,
            routing_category=routing_category,
            limit=3,
        )
        await session.commit()

    async def _generate_and_persist_draft(
        current: EmailTriageState,
        *,
        cross_thread_context: CrossThreadContextSchema | None = None,
        tone_references: list[str] | None = None,
        tone_profile: str | None = None,
        negative_constraints: list[str] | None = None,
        urgency_hints: list[str] | None = None,
    ) -> EmailTriageState:
        if existing is not None:
            current.draft = DraftSchema.model_validate(
                existing.model_dump(include=set(DraftSchema.model_fields))
            )
            current.draft_status = "DRAFTED"
            return current

        if current.triage is None:
            current.draft_status = "REQUIRES_HUMAN"
            current.error_logs.append("draft_skipped:missing_triage")
            return current

        try:
            from app.services.skill_reference_service import make_reference_loader

            reference_loader = None
            if skill_ids:
                reference_loader, _ = make_reference_loader(
                    active_skill_ids=set(skill_ids),
                    session_factory=session_factory,
                )
            from app.services import related_thread_service

            async with session_factory() as assoc_session:
                confirmed_associations = await related_thread_service.load_confirmed_contexts(
                    assoc_session,
                    thread_id,
                )
            generated = await draft_llm.generate_draft(
                current.original_email,
                current.thread_context,
                current.triage,
                client=client,
                settings=settings,
                cross_thread_context=cross_thread_context,
                tone_references=tone_references,
                tone_profile=tone_profile,
                skills=skill_contents,
                negative_constraints=negative_constraints,
                urgency_hints=urgency_hints,
                reference_loader=reference_loader,
                confirmed_associations=confirmed_associations,
            )
        except DraftGenerationError as exc:
            logger.warning(
                "draft_generation_failed",
                conversation_id=email.conversation_id,
                mailbox=email.mailbox,
                message_id=message_id,
                error_type=type(exc).__name__,
            )
            current.draft_status = "REQUIRES_HUMAN"
            current.error_logs.append(f"draft_failed:{type(exc).__name__}")
            return current

        confidence = (
            cross_thread_context.similarity_score if cross_thread_context is not None else None
        )
        async with session_factory() as session, session.begin():
            persisted = await draft_repo.create_draft(
                session,
                thread_id=thread_id,
                message_id=message_id,
                draft=generated.draft,
                prompt_version=generated.prompt_version,
                context_match_confidence=confidence,
                routing_category=(
                    state.triage.routing_category if state.triage is not None else None
                ),
                tool_calls=generated.tool_calls or None,
                applied_skills=applied_skills or None,
            )
            if generated.tool_calls:
                try:
                    await audit_service.log_event(
                        session,
                        event_type="draft.skill_reference_read",
                        conversation_id=email.conversation_id,
                        mailbox=email.mailbox,
                        payload={
                            "draft_id": str(persisted.id),
                            "tool_calls": generated.tool_calls,
                        },
                        actor="system",
                    )
                except Exception:
                    logger.warning(
                        "draft_skill_reference_audit_failed",
                        message_id=message_id,
                    )
            current.draft = DraftSchema.model_validate(
                persisted.model_dump(include=set(DraftSchema.model_fields))
            )
            current.draft_status = "DRAFTED"
            logger.info(
                "draft_persisted",
                conversation_id=email.conversation_id,
                mailbox=email.mailbox,
                message_id=message_id,
                prompt_version=generated.prompt_version,
                urgency=generated.draft.urgency,
                model=generated.model,
                latency_ms=generated.latency_ms,
                context_match_confidence=confidence,
                skills_count=len(skill_contents),
            )
            return current

    if needs_context:
        state = await _generate_and_persist_draft(
            state,
            cross_thread_context=cross_thread,
            tone_references=tone_references,
            tone_profile=tone_profile_block,
            negative_constraints=negative_constraints,
            urgency_hints=urgency_hints,
        )
    else:
        _embed_result, state = await asyncio.gather(
            _embed_in_fresh_session(
                state=state,
                openai_client=openai_client,
                settings=settings,
                session_factory=session_factory,
            ),
            _generate_and_persist_draft(
                state,
                tone_references=tone_references,
                tone_profile=tone_profile_block,
                negative_constraints=negative_constraints,
                urgency_hints=urgency_hints,
            ),
        )
        _ = _embed_result

    draft_event = "draft.generated" if state.draft_status == "DRAFTED" else "draft.requires_human"
    async with session_factory() as session, session.begin():
        await _safe_audit(
            session,
            state=state,
            event_type=draft_event,
            payload=_draft_audit_payload(state),
        )
        await _apply_draft_outcome_state(session, state=state, thread_id=thread_id)

    # --- Slack only after draft row is committed ---
    if state.draft_status == "DRAFTED":
        if post_slack:
            state = await _post_slack_card_after_commit(
                state,
                redis=redis,
                settings=settings,
                session_factory=session_factory,
            )
        else:
            state.slack_delivery = "not_required"

    return state


async def run_post_ingest_triage(
    *,
    redis: Redis,
    settings: Settings,
    ingest_result: IngestResultSchema,
    post_slack: bool = True,
) -> EmailTriageState | None:
    """Run triage+draft+audit+Slack after a successful ingest commit.

    Used by webhook/poll background paths. Returns None when status is ineligible,
    triage failed (``state.triage is None``), draft requires human retry, or an
    unexpected error occurs. Failures are logged and not raised (ingest already
    succeeded).
    """
    if ingest_result.status not in _TRIAGE_ELIGIBLE_STATUSES:
        return None

    try:
        state = await run_phased_after_ingest(
            redis=redis,
            settings=settings,
            ingest_result=ingest_result,
            post_slack=post_slack,
        )
        if not pipeline_ready_for_dedup(state):
            return None
        return state
    except Exception:
        logger.exception(
            "post_ingest_triage_failed",
            message_id=ingest_result.message_id,
            conversation_id=ingest_result.conversation_id,
        )
        return None
