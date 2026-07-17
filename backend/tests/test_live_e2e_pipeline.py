"""Live end-to-end pipeline — Graph → Redis → Postgres → PII scrub → Haiku.

Opt-in (never runs in default CI). Exercises the same path as poll/webhook
ingest through Haiku triage (pre-Sonnet). Prints a staged, plain-language
report with technical detail under each step.

Run from backend/:

    unset ANTHROPIC_API_KEY
    set -a && source .env && set +a
    # ensure DB migrated: .venv/bin/alembic upgrade head
    RUN_LIVE_E2E=1 .venv/bin/pytest tests/test_live_e2e_pipeline.py -vv -s

Optional env:

    LIVE_MAX_MESSAGES=2       # default 2, max 20
    LIVE_LOOKBACK_DAYS=30     # default 30
    LIVE_E2E_KEEP_DEDUP=1     # do not clear Redis dedup before each message
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.dependencies import anthropic_client_from_settings
from app.core.redis_keys import dedup_key, poll_cursor_key
from app.db.session import dispose_engine
from app.graph.auth import GraphAuth
from app.graph.client import GraphClient
from app.llm import triage as triage_llm
from app.llm.pii_redact import scrub_email_for_llm, scrub_thread_for_llm
from app.llm.prompts import PROMPT_VERSION
from app.models.db.audit_event import AuditEvent
from app.repositories import message_repo, thread_repo
from app.services import ingestion_service, pipeline_service
from tests.live_e2e_report import (
    report_audit_stage,
    report_conversation_timeline,
    report_database_stage,
    report_email_header,
    report_fetch_summary,
    report_finale,
    report_haiku_blocked,
    report_haiku_stage,
    report_pii_stage,
    report_redis_final,
    report_redis_stage,
    report_run_header,
    report_skip_triage,
    report_trigger_tech_ids,
)
from tests.live_helpers import (
    env_flag,
    lookback_days,
    lookback_filter,
    max_messages,
    require_graph_settings,
)

pytestmark = [
    pytest.mark.live_e2e,
    pytest.mark.live_graph,
    pytest.mark.live_claude,
]

_TRIAGE_ELIGIBLE = frozenset({"ingested", "retry_triage"})


@pytest.fixture
def live_settings():
    if not env_flag("RUN_LIVE_E2E"):
        pytest.skip("Set RUN_LIVE_E2E=1 to run the live end-to-end pipeline test")
    try:
        return require_graph_settings()
    except RuntimeError as exc:
        pytest.skip(str(exc))


@pytest.fixture
def live_mailbox(live_settings) -> str:
    return live_settings.mailbox_list[0]


async def _redis_snapshot(
    redis: Redis,
    *,
    mailbox: str,
    message_id: str,
) -> dict[str, str | None]:
    keys = [
        dedup_key(mailbox, message_id),
        poll_cursor_key(mailbox),
    ]
    out: dict[str, str | None] = {}
    for key in keys:
        value = await redis.get(key)
        ttl = await redis.ttl(key)
        out[f"{key} (ttl={ttl})"] = value
    return out


async def _load_db_evidence(
    session: AsyncSession,
    *,
    mailbox: str,
    message_id: str,
    conversation_id: str | None,
    thread_id: str | None,
) -> dict[str, Any]:
    thread = None
    if thread_id:
        try:
            thread = await thread_repo.get_by_id(session, uuid.UUID(thread_id))
        except ValueError:
            thread = None
    if thread is None and conversation_id:
        thread = await thread_repo.get_by_conversation_id(session, conversation_id, mailbox=mailbox)

    messages: list[dict[str, object]] = []
    if thread is not None:
        rows = await message_repo.list_by_thread(session, thread.id)
        messages = [
            {
                "graph_message_id": row.graph_message_id,
                "direction": row.direction,
                "sender": row.sender,
                "received_at": row.received_at.isoformat(),
                "body_preview": row.body_preview,
                "body_text": row.body_text,
            }
            for row in rows
        ]

    audit_events: list[dict[str, object]] = []
    if conversation_id:
        stmt = (
            select(AuditEvent)
            .where(
                AuditEvent.conversation_id == conversation_id,
                AuditEvent.mailbox == mailbox,
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(10)
        )
        result = await session.execute(stmt)
        audit_events = [
            {
                "event_type": ev.event_type,
                "actor": ev.actor,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
                "payload": ev.payload,
            }
            for ev in result.scalars().all()
        ]

    trigger = await message_repo.get_by_graph_id(session, message_id)

    return {
        "thread_id": str(thread.id) if thread else None,
        "conversation_id": thread.conversation_id if thread else conversation_id,
        "mailbox": mailbox,
        "subject": thread.subject if thread else None,
        "state": thread.state if thread else None,
        "messages": messages,
        "audit_events": audit_events,
        "trigger_persisted": trigger is not None,
    }


@pytest.mark.asyncio
async def test_live_e2e_graph_ingest_redis_db_pii_haiku(
    live_settings,
    live_mailbox: str,
) -> None:
    """Full pre-Sonnet path: Graph fetch, persist, Redis dedup, PII scrub, Haiku triage."""
    limit = max_messages(2)
    days = lookback_days(30)
    filter_query = lookback_filter(days=days)

    await dispose_engine()
    engine = create_async_engine(live_settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    redis = Redis.from_url(
        live_settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2.0,
        socket_timeout=5.0,
    )
    auth = GraphAuth(live_settings, redis=redis)
    graph = GraphClient(auth)
    anthropic = None

    try:
        assert await redis.ping() is True
        async with session_factory() as session:
            await session.execute(select(1))

        report_run_header(
            mailbox=live_mailbox,
            lookback_days=days,
            max_messages=limit,
            model=live_settings.classification_model,
            redis_url=live_settings.redis_url,
            database_hint=f"...{live_settings.database_url[-40:]}",
            anthropic_configured=bool(live_settings.anthropic_api_key.strip()),
        )

        inbox = await graph.list_messages(
            live_mailbox,
            folder="inbox",
            filter_query=filter_query,
            top=limit,
            orderby="receivedDateTime desc",
        )
        if not inbox:
            pytest.skip(f"No inbox messages in the last {days} days")

        for index, summary in enumerate(inbox, start=1):
            assert summary.id
            message_id = summary.id
            conversation_id = summary.conversation_id
            assert conversation_id, f"message {message_id} missing conversationId"

            report_email_header(
                index=index,
                total=len(inbox),
                subject=summary.subject,
                sender=(
                    summary.from_.email_address.address
                    if summary.from_ and summary.from_.email_address
                    else None
                ),
                received_at=(
                    summary.received_date_time.isoformat() if summary.received_date_time else None
                ),
            )

            if not env_flag("LIVE_E2E_KEEP_DEDUP"):
                key = dedup_key(live_mailbox, message_id)
                deleted = await redis.delete(key)
                if deleted:
                    print("\n  (Cleared previous Redis claim so this run can redo the full path.)")

            before = await _redis_snapshot(redis, mailbox=live_mailbox, message_id=message_id)

            async with session_factory() as session, session.begin():
                ingest_result = await ingestion_service.ingest_graph_message(
                    session=session,
                    redis=redis,
                    graph_client=graph,
                    mailbox=live_mailbox,
                    message_id=message_id,
                )

            after_claim = await _redis_snapshot(redis, mailbox=live_mailbox, message_id=message_id)

            thread_context = ingest_result.thread_context
            trigger = None
            if thread_context and thread_context.messages:
                trigger = next(
                    (m for m in thread_context.messages if m.message_id == message_id),
                    thread_context.messages[-1],
                )

            # ---- Narrative order: 1 fetch → 2 DB → 3 Redis → 4 PII → 5 Haiku → 6 audit
            report_fetch_summary(
                inbox_count=len(inbox),
                thread_message_count=(len(thread_context.messages) if thread_context else 0),
                subject=summary.subject,
            )
            if thread_context:
                report_conversation_timeline(thread_context)
            if trigger:
                report_trigger_tech_ids(trigger)

            async with session_factory() as session:
                db_after_ingest = await _load_db_evidence(
                    session,
                    mailbox=live_mailbox,
                    message_id=message_id,
                    conversation_id=ingest_result.conversation_id or conversation_id,
                    thread_id=ingest_result.thread_id,
                )
            report_database_stage(
                thread_id=db_after_ingest["thread_id"],
                conversation_id=db_after_ingest["conversation_id"],
                mailbox=db_after_ingest["mailbox"],
                subject=db_after_ingest["subject"],
                state=db_after_ingest["state"],
                messages=db_after_ingest["messages"],
                audit_events=db_after_ingest["audit_events"],
                show_message_bodies=True,
            )
            assert db_after_ingest["trigger_persisted"] or ingest_result.status in {
                "duplicate",
                "in_flight",
                "skipped",
            }

            if ingest_result.status not in _TRIAGE_ELIGIBLE:
                report_redis_stage(
                    before=before,
                    after_claim=after_claim,
                    after_complete=None,
                    ingest_status=ingest_result.status,
                )
                report_skip_triage(ingest_result.status)
                continue

            assert thread_context is not None
            assert trigger is not None

            # Step 3 — Redis checklist (before + in-progress; final update after Haiku)
            report_redis_stage(
                before=before,
                after_claim=after_claim,
                after_complete=None,
                ingest_status=ingest_result.status,
            )

            scrubbed_email = scrub_email_for_llm(trigger)
            scrubbed_thread = scrub_thread_for_llm(thread_context)
            user_content = triage_llm._build_user_content(scrubbed_email, scrubbed_thread)
            report_pii_stage(
                trigger=trigger,
                thread=thread_context,
                user_content=user_content,
            )

            async with session_factory() as session:
                db_msg = await message_repo.get_by_graph_id(session, message_id)
            assert db_msg is not None
            assert db_msg.body_text == trigger.body_text

            if not live_settings.anthropic_api_key.strip():
                report_haiku_blocked()
                await ingestion_service.release_ingest_dedup(redis, live_mailbox, message_id)
                pytest.fail(
                    "ANTHROPIC_API_KEY is empty — Graph/Redis/DB/PII evidence printed; "
                    "Haiku cannot run. unset ANTHROPIC_API_KEY then source .env."
                )

            if anthropic is None:
                anthropic = anthropic_client_from_settings(live_settings)

            try:
                async with session_factory() as session, session.begin():
                    state = await pipeline_service.run_after_ingest(
                        session=session,
                        redis=redis,
                        settings=live_settings,
                        client=anthropic,
                        ingest_result=ingest_result,
                    )
            except Exception:
                await ingestion_service.release_ingest_dedup(redis, live_mailbox, message_id)
                raise

            if state.triage is None:
                await ingestion_service.release_ingest_dedup(redis, live_mailbox, message_id)
                report_haiku_stage(
                    model=live_settings.classification_model,
                    prompt_version=PROMPT_VERSION,
                    draft_status=state.draft_status,
                    triage=None,
                )
                pytest.fail(
                    f"Haiku triage failed for message_id={message_id} errors={state.error_logs}"
                )

            await ingestion_service.complete_ingest_dedup(redis, live_mailbox, message_id)
            after_complete = await _redis_snapshot(
                redis, mailbox=live_mailbox, message_id=message_id
            )

            report_haiku_stage(
                model=live_settings.classification_model,
                prompt_version=PROMPT_VERSION,
                draft_status=state.draft_status,
                triage=state.triage,
            )
            report_redis_final(after_complete)

            async with session_factory() as session:
                db_final = await _load_db_evidence(
                    session,
                    mailbox=live_mailbox,
                    message_id=message_id,
                    conversation_id=ingest_result.conversation_id or conversation_id,
                    thread_id=ingest_result.thread_id,
                )
            report_audit_stage(audit_events=db_final["audit_events"])

            assert isinstance(state.triage.is_spam, bool)
            assert isinstance(state.triage.has_action_items, bool)
            assert isinstance(state.triage.needs_context, bool)
            assert state.draft_status in {"PENDING", "SKIPPED", "REQUIRES_HUMAN"}
            if state.triage.is_spam or not state.triage.has_action_items:
                assert state.draft_status == "SKIPPED"
            else:
                assert state.draft_status == "PENDING"
            assert db_final["messages"], "expected persisted thread messages"
            assert any(ev["event_type"].startswith("triage.") for ev in db_final["audit_events"]), (
                "expected a triage.* audit event"
            )
            completed_values = [v for k, v in after_complete.items() if "dedup:" in k]
            assert completed_values and completed_values[0] == "completed"

        report_finale(
            mailbox=live_mailbox,
            poll_cursor_key=poll_cursor_key(live_mailbox),
        )
    finally:
        await graph.aclose()
        if anthropic is not None:
            await anthropic.close()
        await redis.aclose()
        await engine.dispose()
        await dispose_engine()
