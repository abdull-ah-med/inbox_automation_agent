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
from app.graph.client import GraphClient
from app.llm import draft_generator as draft_llm
from app.llm.prompts import PROMPT_VERSION
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema, EmailTriageState
from app.models.schemas.graph import IngestResultSchema
from app.repositories import draft_repo
from app.services import (
    audit_service,
    context_service,
    draft_service,
    embedding_service,
    slack_service,
    triage_service,
)
from app.services.triage_service import decide_triage_outcome

logger = structlog.get_logger(__name__)

_OUTCOME_EVENT_TYPES = {
    "spam_discarded": "triage.spam_discarded",
    "no_action_discarded": "triage.no_action_discarded",
    "action_needed": "triage.action_needed",
}

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


def _should_embed(state: EmailTriageState) -> bool:
    """Embed every non-spam email that reached a triage result."""
    return state.triage is not None and not state.triage.is_spam


async def _embed_non_spam_safe(
    session: AsyncSession,
    *,
    state: EmailTriageState,
    openai_client: AsyncOpenAI,
    settings: Settings,
) -> None:
    if not _should_embed(state):
        return
    await embedding_service.embed_and_store_safe(
        session,
        email=state.original_email,
        client=openai_client,
        settings=settings,
    )


async def _embed_in_fresh_session(
    *,
    state: EmailTriageState,
    openai_client: AsyncOpenAI,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Embed in its own short transaction (safe to gather with draft work)."""
    if not _should_embed(state):
        return
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
    state = await triage_service.run_triage(state, client=client, settings=settings)

    await _safe_audit(
        session,
        state=state,
        event_type=_audit_event_type(state),
        payload=_audit_payload(state),
    )

    from app.core.dependencies import openai_client_from_settings
    from app.db.session import get_session_factory

    embed_client = openai_client or openai_client_from_settings(settings)
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
    if needs_context:
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
        # Ensure Day-4 embed even when Flow B failed before store.
        if cross_thread is None:
            await _embed_non_spam_safe(
                session,
                state=state,
                openai_client=embed_client,
                settings=settings,
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

    state = await draft_service.run_draft(
        state,
        session=session,
        client=client,
        settings=settings,
        thread_id=uuid.UUID(ingest_result.thread_id),
        cross_thread_context=cross_thread,
    )

    draft_event = "draft.generated" if state.draft_status == "DRAFTED" else "draft.requires_human"
    await _safe_audit(
        session,
        state=state,
        event_type=draft_event,
        payload=_draft_audit_payload(state),
    )
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
    openai_client: AsyncOpenAI,
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

    # --- LLM: triage (no DB connection held) ---
    state = await triage_service.run_triage(state, client=client, settings=settings)

    async with session_factory() as session, session.begin():
        await _safe_audit(
            session,
            state=state,
            event_type=_audit_event_type(state),
            payload=_audit_payload(state),
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
    if needs_context:
        # OpenAI + Graph run inside context_service without holding a DB txn.
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

    # --- Short read for draft idempotency, then release before Sonnet ---
    async with session_factory() as session:
        existing = await draft_repo.get_draft_by_message(session, message_id=message_id)
        await session.commit()

    async def _generate_and_persist_draft(
        current: EmailTriageState,
        *,
        cross_thread_context: CrossThreadContextSchema | None = None,
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
            generated = await draft_llm.generate_draft(
                current.original_email,
                current.thread_context,
                current.triage,
                client=client,
                settings=settings,
                cross_thread_context=cross_thread_context,
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
            )
        return current

    if needs_context:
        state = await _generate_and_persist_draft(
            state,
            cross_thread_context=cross_thread,
        )
    else:
        _embed_result, state = await asyncio.gather(
            _embed_in_fresh_session(
                state=state,
                openai_client=openai_client,
                settings=settings,
                session_factory=session_factory,
            ),
            _generate_and_persist_draft(state),
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
