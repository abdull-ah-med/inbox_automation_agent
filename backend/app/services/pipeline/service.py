"""Post-ingest pipeline — triage, then Sonnet draft when action is needed."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import DraftGenerationError
from app.graph.client import GraphClient
from app.llm import draft_generator as draft_llm
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema, EmailTriageState
from app.models.schemas.graph import IngestResultSchema
from app.repositories import (
    draft_repo,
    message_repo,
    sent_reply_repo,  # noqa: F401 — tests patch pipeline.service.sent_reply_repo
    skill_repo,
    thread_repo,  # noqa: F401 — tests patch pipeline.service.thread_repo
)
from app.services import (
    audit_service,
    context_service,
    draft_service,
    embedding_service,
    reply_memory_service,
    sent_reply_service,
    skill_selection_service,
    summary_service,
    tone_profile_service,
    triage_service,
    urgency_feedback_service,
)
from app.services.feedback_atom_service import record_retrieval_hits
from app.services.feedback_draft_context import load_legacy_negative_constraints
from app.services.pipeline.already_replied import (
    latest_proposed_has_teaching_note,
    promote_and_resolve_sent_tip,
)
from app.services.pipeline.already_replied import (
    select_original_email as _select_original_email,
)
from app.services.pipeline.audit_helpers import context_audit_payload as _context_audit_payload
from app.services.pipeline.audit_helpers import safe_audit
from app.services.pipeline.draft_phase import (
    _apply_draft_outcome_state,
    _draft_audit_payload,
)
from app.services.pipeline.draft_phase import (
    post_slack_card_after_commit as _post_slack_card_after_commit,
)
from app.services.pipeline.feedback_context import (
    append_recurrence_hint as _phased_append_recurrence_hint,
)
from app.services.pipeline.feedback_context import (
    extract_thread_context_safe as _extract_thread_context_safe,
)
from app.services.pipeline.feedback_context import (
    load_draft_side_context,
    load_paired_draft_pack,
)
from app.services.pipeline.thread_lifecycle import (
    apply_draftassistant_auto_resolve,
    reopen_finished_if_action_needed,
)
from app.services.pipeline.triage_phase import (
    _allowlisted_senders,
    _apply_triage_outcome_state,
    _audit_event_type,
    _audit_payload,
    _invalidate_chat_cache_mailbox,
)
from app.services.urgency_prediction_service import log_prediction
from app.services.urgency_rule_service import apply_after_model

_safe_audit = safe_audit  # tests patch pipeline.service._safe_audit

logger = structlog.get_logger(__name__)


# Spam / no-action skips are always terminal for dedup.
# DRAFTED is terminal only when Slack delivery is in this set.
_SLACK_OK_FOR_DEDUP = frozenset(
    {"posted", "already_posted", "skipped_unconfigured", "not_required"}
)


def pipeline_ready_for_dedup(state: EmailTriageState) -> bool:
    """True when webhook/poll may mark ingest dedup completed.

    REQUIRES_HUMAN stays open for retry. SKIPPED is terminal. DRAFTED completes
    only when Slack delivery is posted, already posted, unconfigured, or not required.
    """
    if state.triage is None:
        return False
    if state.draft_status == "SKIPPED":
        return True
    if state.draft_status == "DRAFTED":
        return state.slack_delivery in _SLACK_OK_FOR_DEDUP
    return False


def slack_review_card_required(state: EmailTriageState) -> bool:
    """Approve/Reject Slack cards are only for letters, not empty briefing bodies."""
    if state.draft_status != "DRAFTED" or state.draft is None:
        return False
    return bool((state.draft.reply_body or "").strip())


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
    if openai_client is None:
        raise RuntimeError("openai_client required when embedding is enabled")
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
    if openai_client is None:
        raise RuntimeError("openai_client required when embedding is enabled")
    async with session_factory() as session, session.begin():
        await embedding_service.embed_and_store_safe(
            session,
            email=state.original_email,
            client=openai_client,
            settings=settings,
        )


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

    await safe_audit(
        session,
        state=state,
        event_type=_audit_event_type(state),
        payload=_audit_payload(state),
    )
    await _apply_triage_outcome_state(session, state=state, thread_id=parsed_thread_id)
    await reopen_finished_if_action_needed(
        session,
        state=state,
        thread_id=parsed_thread_id,
    )
    draftassistant_closed = await apply_draftassistant_auto_resolve(
        session,
        state=state,
        thread_id=parsed_thread_id,
        settings=settings,
    )
    if draftassistant_closed is not None:
        from app.core.dependencies import openai_client_from_settings

        embed_client = openai_client or openai_client_from_settings(settings)
        await _embed_non_spam_safe(
            session,
            state=draftassistant_closed,
            openai_client=embed_client,
            settings=settings,
        )
        return draftassistant_closed

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
            await _extract_thread_context_safe(
                thread_id=parsed_thread_id,
                client=client,
                settings=settings,
                session_factory=factory,
            )
    needs_context = bool(state.triage and state.triage.needs_context and state.triage.draft_needed)

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
        await safe_audit(
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
        await safe_audit(
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
    await safe_audit(
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


@dataclass
class _DraftPhaseInputs:
    existing: object | None
    skill_contents: list[str]
    skill_ids: list[uuid.UUID]
    applied_skills: list
    tone_references: list[str]
    tone_profile_block: str | None
    negative_constraints: list[str]
    paired_examples: list[dict[str, str | None]]
    urgency_hints: list[str]
    retrieved_atom_ids: list[uuid.UUID]
    retrieved_note_ids: list[uuid.UUID]
    fix_atom_payloads: list[dict]


async def _phased_triage_summarize(
    *,
    state: EmailTriageState,
    email: EmailMessageSchema,
    client: AsyncAnthropic,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    parsed_thread_id: uuid.UUID | None,
    apply_thread_state: bool = True,
) -> EmailTriageState:
    async with session_factory() as session:
        allowlisted = await _allowlisted_senders(session, email.mailbox)

    # --- LLM: triage (no DB connection held) ---
    state = await triage_service.run_triage(
        state,
        client=client,
        settings=settings,
        allowlisted_senders=allowlisted,
    )

    async with session_factory() as session, session.begin():
        await _invalidate_chat_cache_mailbox(session, email.mailbox)
        await safe_audit(
            session,
            state=state,
            event_type=_audit_event_type(state),
            payload=_audit_payload(state),
        )
        await _apply_triage_outcome_state(
            session,
            state=state,
            thread_id=parsed_thread_id,
            apply_thread_state=apply_thread_state,
        )

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
            await _extract_thread_context_safe(
                thread_id=parsed_thread_id,
                client=client,
                settings=settings,
                session_factory=session_factory,
            )
    return state


async def _phased_already_replied_exit(
    *,
    state: EmailTriageState,
    thread_id: uuid.UUID,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    anthropic_client: AsyncAnthropic | None = None,
) -> EmailTriageState:
    """Skip Sonnet when a briefing already exists; learn from the Outlook send."""
    state.draft_status = "SKIPPED"
    state.slack_delivery = "not_required"
    await _embed_in_fresh_session(
        state=state,
        openai_client=openai_client,
        settings=settings,
        session_factory=session_factory,
    )
    async with session_factory() as session, session.begin():
        await safe_audit(
            session,
            state=state,
            event_type="draft.skipped_already_replied",
            payload={
                "draft_status": state.draft_status,
                "reason": "already_replied",
            },
        )
    await promote_and_resolve_sent_tip(
        state=state,
        thread_id=thread_id,
        openai_client=openai_client,
        settings=settings,
        session_factory=session_factory,
        anthropic_client=anthropic_client,
    )
    return state


async def _phased_early_draft_exit(
    *,
    state: EmailTriageState,
    ingest_result: IngestResultSchema,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> EmailTriageState | None:
    """Return a terminal state when draft work should not continue; else None."""
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
            await safe_audit(
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
    return None


async def _phased_resolve_cross_thread(
    *,
    state: EmailTriageState,
    email: EmailMessageSchema,
    message_id: str,
    needs_context: bool,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    graph_client: GraphClient | None,
) -> CrossThreadContextSchema | None:
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
            await safe_audit(
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
    return cross_thread


async def _phased_load_draft_inputs(
    *,
    state: EmailTriageState,
    email: EmailMessageSchema,
    message_id: str,
    thread_id: uuid.UUID,
    client: AsyncAnthropic,
    openai_client: AsyncOpenAI | None,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> _DraftPhaseInputs:
    skill_contents: list[str] = []
    skill_ids: list[uuid.UUID] = []
    applied_skills: list = []

    letter = state.triage is not None and state.triage.draft_needed
    async with session_factory() as session:
        existing = await draft_repo.get_draft_by_message(session, message_id=message_id)
        active_skills: list = []
        if letter:
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

    if not letter:
        return _DraftPhaseInputs(
            existing=existing,
            skill_contents=[],
            skill_ids=[],
            applied_skills=[],
            tone_references=[],
            tone_profile_block=None,
            negative_constraints=[],
            paired_examples=[],
            urgency_hints=[],
            retrieved_atom_ids=[],
            retrieved_note_ids=[],
            fix_atom_payloads=[],
        )

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

    email_text = f"{email.subject}\n\n{email.body_clean or email.body_text}"
    routing_category = state.triage.routing_category if state.triage is not None else "general"

    async with session_factory() as session:
        tone_profile_block, tone_references = await tone_profile_service.load_for_draft(
            session,
            openai_client=openai_client,
            settings=settings,
            mailbox=email.mailbox,
            routing_category=routing_category,
            email_text=email_text,
        )
        negative_constraints = await load_legacy_negative_constraints(
            session,
            settings=settings,
            openai_client=openai_client,
            mailbox=email.mailbox,
            email_text=email_text,
            routing_category=routing_category,
        )
        (
            negative_constraints,
            retrieved_atom_ids,
            retrieved_note_ids,
            fix_atom_payloads,
            paired_examples,
        ) = await load_paired_draft_pack(
            session,
            settings=settings,
            openai_client=openai_client,
            anthropic_client=client,
            mailbox=email.mailbox,
            email_text=email_text,
            sender=email.sender or "",
            routing_category=routing_category,
            negative_constraints=negative_constraints,
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
        urgency_hints = await _phased_append_recurrence_hint(
            session,
            email=email,
            thread_id=thread_id,
            urgency_hints=urgency_hints,
        )
        await session.commit()

    return _DraftPhaseInputs(
        existing=existing,
        skill_contents=skill_contents,
        skill_ids=skill_ids,
        applied_skills=applied_skills,
        tone_references=tone_references,
        tone_profile_block=tone_profile_block,
        negative_constraints=negative_constraints,
        paired_examples=paired_examples,
        urgency_hints=urgency_hints,
        retrieved_atom_ids=retrieved_atom_ids,
        retrieved_note_ids=retrieved_note_ids,
        fix_atom_payloads=fix_atom_payloads,
    )


async def _phased_generate_and_persist_draft(
    current: EmailTriageState,
    *,
    inputs: _DraftPhaseInputs,
    email: EmailMessageSchema,
    message_id: str,
    thread_id: uuid.UUID,
    client: AsyncAnthropic,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    cross_thread_context: CrossThreadContextSchema | None = None,
    tone_references: list[str] | None = None,
    tone_profile: str | None = None,
    negative_constraints: list[str] | None = None,
    paired_examples: list[dict[str, str | None]] | None = None,
    urgency_hints: list[str] | None = None,
) -> EmailTriageState:
    if inputs.existing is not None:
        current.draft = DraftSchema.model_validate(
            inputs.existing.model_dump(include=set(DraftSchema.model_fields))
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
        confirmed_associations: list = []
        directory: dict[str, str] | None = None
        working_memory: dict = {}
        if current.triage.draft_needed:
            if inputs.skill_ids:
                reference_loader, _ = make_reference_loader(
                    active_skill_ids=set(inputs.skill_ids),
                    session_factory=session_factory,
                )
            async with session_factory() as assoc_session:
                confirmed_associations, directory, working_memory = await load_draft_side_context(
                    assoc_session,
                    thread_id=thread_id,
                    settings=settings,
                    mailbox=email.mailbox,
                    thread_context=current.thread_context,
                    current_email=current.original_email,
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
            skills=inputs.skill_contents,
            negative_constraints=negative_constraints,
            paired_examples=paired_examples,
            urgency_hints=urgency_hints,
            reference_loader=reference_loader,
            confirmed_associations=confirmed_associations,
            directory=directory,
            user_notes=working_memory.get("user_notes", ""),
            facts=working_memory.get("facts") or None,
            prior_sends=working_memory.get("prior_sends") or None,
            org_identity=working_memory.get("org_identity", ""),
        )
        from app.services.draft_validator_service import validate_and_maybe_retry

        email_text = f"{email.subject}\n\n{email.body_clean or email.body_text or ''}"
        validated = await validate_and_maybe_retry(
            client,
            settings,
            draft_body=generated.draft.reply_body,
            fix_atoms=inputs.fix_atom_payloads,
            email_text=email_text,
        )
        if validated.body != generated.draft.reply_body:
            generated.draft = generated.draft.model_copy(update={"reply_body": validated.body})
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

    predicted = generated.draft.urgency
    sender_addr = (email.sender or "").strip()
    sender_dom = sender_addr.split("@")[-1] if "@" in sender_addr else ""
    body_text = email.body_clean or email.body_text or ""
    async with session_factory() as rule_session:
        decision = await apply_after_model(
            rule_session,
            mailbox=email.mailbox,
            predicted_urgency=predicted,
            sender_domain=sender_dom,
            alert_fingerprint=None,
            body_text=body_text,
            settings=settings,
        )
        if rule_session.in_transaction():
            await rule_session.commit()
    if decision.final_urgency != predicted:
        generated.draft = generated.draft.model_copy(update={"urgency": decision.final_urgency})

    confidence = cross_thread_context.similarity_score if cross_thread_context is not None else None
    async with session_factory() as session, session.begin():
        persisted = await draft_repo.create_draft(
            session,
            thread_id=thread_id,
            message_id=message_id,
            draft=generated.draft,
            prompt_version=generated.prompt_version,
            context_match_confidence=confidence,
            routing_category=(
                current.triage.routing_category if current.triage is not None else None
            ),
            tool_calls=generated.tool_calls or None,
            applied_skills=inputs.applied_skills or None,
            retrieved_atom_ids=inputs.retrieved_atom_ids,
            retrieved_note_ids=inputs.retrieved_note_ids,
        )
        await record_retrieval_hits(
            session,
            atom_ids=inputs.retrieved_atom_ids,
            note_ids=inputs.retrieved_note_ids,
        )
        await log_prediction(
            session,
            draft_id=persisted.id,
            thread_id=thread_id,
            mailbox=email.mailbox,
            sender_domain=sender_dom or "",
            predicted_urgency=predicted,
            final_urgency=decision.final_urgency,
            probs=current.triage.probs if current.triage is not None else None,
            routing_category=(
                current.triage.routing_category if current.triage is not None else None
            ),
            applied_rule_ids=decision.applied_rule_ids,
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
            skills_count=len(inputs.skill_contents),
        )
        return current


async def _phased_finalize_draft_and_slack(
    *,
    state: EmailTriageState,
    thread_id: uuid.UUID,
    redis: Redis,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    post_slack: bool,
) -> EmailTriageState:
    draft_event = "draft.generated" if state.draft_status == "DRAFTED" else "draft.requires_human"
    async with session_factory() as session, session.begin():
        await safe_audit(
            session,
            state=state,
            event_type=draft_event,
            payload=_draft_audit_payload(state),
        )
        await _apply_draft_outcome_state(session, state=state, thread_id=thread_id)

    # --- Slack only after draft row is committed ---
    if state.draft_status == "DRAFTED":
        if post_slack and slack_review_card_required(state):
            state = await _post_slack_card_after_commit(
                state,
                redis=redis,
                settings=settings,
                session_factory=session_factory,
            )
        else:
            state.slack_delivery = "not_required"
    return state


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
    parsed_thread_id = uuid.UUID(ingest_result.thread_id) if ingest_result.thread_id else None

    already_replied = False
    skip_letter = False
    has_teaching_note = False
    skip_briefing_extras = False
    if parsed_thread_id is not None:
        async with session_factory() as session:
            already_replied = await sent_reply_service.thread_tip_already_replied(
                session,
                parsed_thread_id,
            )
            skip_letter = already_replied
            if not skip_letter:
                skip_letter = await sent_reply_service.graph_outbound_tip_in_sync(
                    session,
                    graph_client,
                    mailbox=context.mailbox,
                    conversation_id=context.conversation_id,
                    thread_id=parsed_thread_id,
                    trigger_graph_message_id=ingest_result.message_id,
                )
            if skip_letter:
                has_teaching_note = await latest_proposed_has_teaching_note(
                    session,
                    parsed_thread_id,
                )
                if not has_teaching_note:
                    rows = await message_repo.list_by_thread(session, parsed_thread_id)
                    has_inbound = any(row.direction == "inbound" for row in rows)
                    tip = max(rows, key=lambda row: row.received_at) if rows else None
                    is_meeting = tip is not None and sent_reply_service.is_meeting_message(tip)
                    skip_briefing_extras = is_meeting or not has_inbound

    state = await _phased_triage_summarize(
        state=state,
        email=email,
        client=client,
        settings=settings,
        session_factory=session_factory,
        parsed_thread_id=parsed_thread_id,
        apply_thread_state=not skip_letter,
    )

    if parsed_thread_id is not None:
        async with session_factory() as session, session.begin():
            await reopen_finished_if_action_needed(
                session,
                state=state,
                thread_id=parsed_thread_id,
            )
            draftassistant_closed = await apply_draftassistant_auto_resolve(
                session,
                state=state,
                thread_id=parsed_thread_id,
                settings=settings,
            )
        if draftassistant_closed is not None:
            await _embed_in_fresh_session(
                state=draftassistant_closed,
                openai_client=openai_client,
                settings=settings,
                session_factory=session_factory,
            )
            return draftassistant_closed

    skip_sonnet = skip_letter and (
        has_teaching_note
        or skip_briefing_extras
        or (state.triage is not None and state.triage.is_spam)
    )
    if skip_sonnet and parsed_thread_id is not None:
        return await _phased_already_replied_exit(
            state=state,
            thread_id=parsed_thread_id,
            openai_client=openai_client,
            settings=settings,
            session_factory=session_factory,
            anthropic_client=client,
        )

    if skip_letter and state.triage is not None:
        state.triage.draft_needed = False
        if state.draft_status != "SKIPPED":
            state.draft_status = "PENDING"

    early = await _phased_early_draft_exit(
        state=state,
        ingest_result=ingest_result,
        openai_client=openai_client,
        settings=settings,
        session_factory=session_factory,
    )
    if early is not None:
        return early

    thread_id = uuid.UUID(ingest_result.thread_id)
    message_id = email.message_id
    needs_context = bool(state.triage and state.triage.needs_context and state.triage.draft_needed)
    cross_thread = await _phased_resolve_cross_thread(
        state=state,
        email=email,
        message_id=message_id,
        needs_context=needs_context,
        openai_client=openai_client,
        settings=settings,
        session_factory=session_factory,
        graph_client=graph_client,
    )

    # --- Short DB reads, then release before OpenAI / Haiku ---
    inputs = await _phased_load_draft_inputs(
        state=state,
        email=email,
        message_id=message_id,
        thread_id=thread_id,
        client=client,
        openai_client=openai_client,
        settings=settings,
        session_factory=session_factory,
    )

    draft_kwargs = {
        "inputs": inputs,
        "email": email,
        "message_id": message_id,
        "thread_id": thread_id,
        "client": client,
        "settings": settings,
        "session_factory": session_factory,
        "tone_references": inputs.tone_references,
        "tone_profile": inputs.tone_profile_block,
        "negative_constraints": inputs.negative_constraints,
        "paired_examples": inputs.paired_examples,
        "urgency_hints": inputs.urgency_hints,
    }
    if needs_context:
        state = await _phased_generate_and_persist_draft(
            state,
            cross_thread_context=cross_thread,
            **draft_kwargs,
        )
    else:
        _embed_result, state = await asyncio.gather(
            _embed_in_fresh_session(
                state=state,
                openai_client=openai_client,
                settings=settings,
                session_factory=session_factory,
            ),
            _phased_generate_and_persist_draft(state, **draft_kwargs),
        )
        _ = _embed_result

    state = await _phased_finalize_draft_and_slack(
        state=state,
        thread_id=thread_id,
        redis=redis,
        settings=settings,
        session_factory=session_factory,
        post_slack=post_slack and not skip_letter,
    )
    if skip_letter:
        await promote_and_resolve_sent_tip(
            state=state,
            thread_id=thread_id,
            openai_client=openai_client,
            settings=settings,
            session_factory=session_factory,
            anthropic_client=client,
        )
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
