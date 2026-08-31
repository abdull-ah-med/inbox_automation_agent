"""Live end-to-end pipeline — Graph → Redis → Postgres → PII → Haiku → draft → Slack.

Opt-in (never runs in default CI). Fetches inbox mail from every address in
``TEST_MAILBOXES`` (falls back to ``TARGET_MAILBOXES``), runs the production
ingest + triage + draft path, and posts Slack review cards for every
action-needed ``DRAFTED`` message (same gate as production).

Run from backend/:

    unset ANTHROPIC_API_KEY
    set -a && source .env && set +a
    # ensure DB migrated: .venv/bin/alembic upgrade head
    RUN_LIVE_E2E=1 .venv/bin/pytest tests/test_live_e2e_pipeline.py -vv -s

Optional env:

    TEST_MAILBOXES=a@x.com,b@x.com   # override TARGET_MAILBOXES for this test
    LIVE_MAX_MESSAGES=3              # default: 3 inbox msgs per mailbox
    LIVE_MAX_MESSAGES=all            # or process all msgs in lookback (paginated)
    LIVE_LOOKBACK_DAYS=30            # default 30
    LIVE_E2E_KEEP_DEDUP=1            # do not clear Redis dedup / slack locks
    LIVE_E2E_JSON_PATH=...           # override JSON output path

Requires Slack env vars to actually post cards:
    SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, SLACK_REVIEW_CHANNEL_ID
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from anthropic import AsyncAnthropic
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.dependencies import anthropic_client_from_settings, get_slack_app
from app.core.exceptions import GraphClientError
from app.core.redis_keys import dedup_key, slack_posted_key
from app.core.tenant_scope import TenantScope
from app.db.session import dispose_engine
from app.graph.auth import GraphAuth
from app.graph.client import GraphClient
from app.llm import triage as triage_llm
from app.llm.pii_redact import scrub_email_for_llm, scrub_thread_for_llm
from app.llm.prompts import PROMPT_VERSION
from app.models.db.audit_event import AuditEvent
from app.models.schemas.email_triage_state import EmailTriageState
from app.models.schemas.graph import GraphMessageSchema
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
    report_slack_stage,
    report_trigger_tech_ids,
)
from tests.live_helpers import (
    env_flag,
    lookback_days,
    lookback_filter,
    max_messages,
    require_graph_settings,
    resolve_e2e_mailboxes,
)

_DEFAULT_JSON_PATH = Path(__file__).resolve().parent / "artifacts" / "live_e2e_output.json"


def _json_output_path() -> Path:
    raw = os.environ.get("LIVE_E2E_JSON_PATH", "").strip()
    return Path(raw).expanduser() if raw else _DEFAULT_JSON_PATH


def _write_live_e2e_json(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _slack_configured(settings: Settings) -> bool:
    return bool(
        settings.slack_bot_token.strip()
        and settings.slack_signing_secret.strip()
        and settings.slack_review_channel_id.strip()
    )


def _should_post_slack_review(state: EmailTriageState) -> bool:
    """Match production: post whenever a draft is ready for human review."""
    triage = state.triage
    if triage is None:
        return False
    if triage.is_spam or not triage.has_action_items:
        return False
    return state.draft_status == "DRAFTED"


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
def live_mailboxes(live_settings) -> list[str]:
    try:
        return resolve_e2e_mailboxes(live_settings)
    except RuntimeError as exc:
        pytest.skip(str(exc))


async def _redis_snapshot(
    redis: Redis,
    *,
    mailbox: str,
    message_id: str,
) -> dict[str, str | None]:
    keys = [
        dedup_key(mailbox, message_id),
        slack_posted_key(mailbox, message_id),
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
            thread = await thread_repo.get_by_id(
                session,
                uuid.UUID(thread_id),
                TenantScope.single(mailbox),
            )
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
                "direction": getattr(row.direction, "value", row.direction),
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


async def _fetch_inbox(
    graph: GraphClient,
    mailbox: str,
    *,
    filter_query: str,
    limit: int | None,
) -> list[GraphMessageSchema]:
    """List inbox messages. ``limit is None`` paginates through the full lookback window."""
    if limit is None:
        return await graph.list_messages(
            mailbox,
            folder="inbox",
            filter_query=filter_query,
            top=50,
            orderby="receivedDateTime desc",
            follow_next_link=True,
        )
    return await graph.list_messages(
        mailbox,
        folder="inbox",
        filter_query=filter_query,
        top=limit,
        orderby="receivedDateTime desc",
        follow_next_link=False,
    )


def _is_graph_mailbox_access_denied(exc: GraphClientError) -> bool:
    text = str(exc)
    return "403" in text or "ErrorAccessDenied" in text or "Access is denied" in text


async def _process_one_message(
    *,
    mailbox: str,
    summary: GraphMessageSchema,
    index: int,
    total: int,
    inbox_count: int,
    live_settings: Settings,
    redis: Redis,
    graph: GraphClient,
    session_factory: async_sessionmaker[AsyncSession],
    anthropic: AsyncAnthropic,
) -> dict[str, Any]:
    assert summary.id
    message_id = summary.id
    conversation_id = summary.conversation_id
    assert conversation_id, f"message {message_id} missing conversationId"

    sender = (
        summary.from_.email_address.address
        if summary.from_ and summary.from_.email_address
        else None
    )
    received_at = summary.received_date_time.isoformat() if summary.received_date_time else None

    report_email_header(
        index=index,
        total=total,
        subject=summary.subject,
        sender=sender,
        received_at=received_at,
    )

    record: dict[str, Any] = {
        "mailbox": mailbox,
        "index": index,
        "subject": summary.subject,
        "sender": sender,
        "received_at": received_at,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "ingest_status": None,
        "skipped_triage": False,
        "redis": {},
        "database": None,
        "pii": None,
        "triage": None,
        "draft_status": None,
        "error_logs": None,
        "slack_posted": False,
        "slack_message_ts": None,
        "slack_skip_reason": None,
    }

    if not env_flag("LIVE_E2E_KEEP_DEDUP"):
        deleted_dedup = await redis.delete(dedup_key(mailbox, message_id))
        deleted_slack = await redis.delete(slack_posted_key(mailbox, message_id))
        if deleted_dedup or deleted_slack:
            print("\n  (Cleared previous Redis dedup/Slack locks so this run can redo the path.)")

    before = await _redis_snapshot(redis, mailbox=mailbox, message_id=message_id)

    async with session_factory() as session, session.begin():
        ingest_result = await ingestion_service.ingest_graph_message(
            session=session,
            redis=redis,
            graph_client=graph,
            mailbox=mailbox,
            message_id=message_id,
        )

    after_claim = await _redis_snapshot(redis, mailbox=mailbox, message_id=message_id)
    record["ingest_status"] = ingest_result.status
    record["redis"] = {
        "before": before,
        "after_claim": after_claim,
        "after_complete": None,
    }

    thread_context = ingest_result.thread_context
    trigger = None
    if thread_context and thread_context.messages:
        trigger = next(
            (m for m in thread_context.messages if m.message_id == message_id),
            thread_context.messages[-1],
        )

    report_fetch_summary(
        inbox_count=inbox_count,
        thread_message_count=(len(thread_context.messages) if thread_context else 0),
        subject=summary.subject,
    )
    if thread_context:
        report_conversation_timeline(thread_context)
    if trigger:
        report_trigger_tech_ids(trigger)
        record["trigger"] = trigger.model_dump(mode="json")

    async with session_factory() as session:
        db_after_ingest = await _load_db_evidence(
            session,
            mailbox=mailbox,
            message_id=message_id,
            conversation_id=ingest_result.conversation_id or conversation_id,
            thread_id=ingest_result.thread_id,
        )
    record["database"] = db_after_ingest
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
        record["skipped_triage"] = True
        report_redis_stage(
            before=before,
            after_claim=after_claim,
            after_complete=None,
            ingest_status=ingest_result.status,
        )
        report_skip_triage(ingest_result.status)
        record["slack_skip_reason"] = f"ingest_status={ingest_result.status}"
        report_slack_stage(
            posted=False,
            skipped_reason=record["slack_skip_reason"],
            message_ts=None,
        )
        return record

    assert thread_context is not None
    assert trigger is not None

    report_redis_stage(
        before=before,
        after_claim=after_claim,
        after_complete=None,
        ingest_status=ingest_result.status,
    )

    scrubbed_email = scrub_email_for_llm(trigger)
    scrubbed_thread = scrub_thread_for_llm(thread_context)
    user_content = triage_llm._build_user_content(scrubbed_email, scrubbed_thread)
    record["pii"] = {
        "scrubbed_trigger_body": scrubbed_email.body_text,
        "user_content": user_content,
        "thread_message_count": len(scrubbed_thread.messages),
    }
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
        await ingestion_service.release_ingest_dedup(redis, mailbox, message_id)
        pytest.fail(
            "ANTHROPIC_API_KEY is empty — Graph/Redis/DB/PII evidence printed; "
            "Haiku cannot run. unset ANTHROPIC_API_KEY then source .env."
        )

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
        await ingestion_service.release_ingest_dedup(redis, mailbox, message_id)
        raise

    record["draft_status"] = state.draft_status
    record["error_logs"] = state.error_logs

    if state.triage is None:
        await ingestion_service.release_ingest_dedup(redis, mailbox, message_id)
        report_haiku_stage(
            model=live_settings.classification_model,
            prompt_version=PROMPT_VERSION,
            draft_status=state.draft_status,
            triage=None,
        )
        pytest.fail(f"Haiku triage failed for message_id={message_id} errors={state.error_logs}")

    if pipeline_service.pipeline_ready_for_dedup(state):
        await ingestion_service.complete_ingest_dedup(redis, mailbox, message_id)
    else:
        await ingestion_service.release_ingest_dedup(redis, mailbox, message_id)

    after_complete = await _redis_snapshot(redis, mailbox=mailbox, message_id=message_id)
    record["redis"]["after_complete"] = after_complete
    record["triage"] = state.triage.model_dump(mode="json")

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
            mailbox=mailbox,
            message_id=message_id,
            conversation_id=ingest_result.conversation_id or conversation_id,
            thread_id=ingest_result.thread_id,
        )
    record["database"] = db_final
    report_audit_stage(audit_events=db_final["audit_events"])

    assert isinstance(state.triage.is_spam, bool)
    assert isinstance(state.triage.has_action_items, bool)
    assert isinstance(state.triage.needs_context, bool)
    assert state.draft_status in {"SKIPPED", "DRAFTED", "REQUIRES_HUMAN"}
    if state.triage.is_spam or not state.triage.has_action_items:
        assert state.draft_status == "SKIPPED"
    elif state.draft_status == "REQUIRES_HUMAN":
        pass  # draft/LLM failure — retried via released dedup
    else:
        assert state.draft_status == "DRAFTED"
    assert db_final["messages"], "expected persisted thread messages"
    assert any(ev["event_type"].startswith("triage.") for ev in db_final["audit_events"]), (
        "expected a triage.* audit event"
    )

    # Slack: same as production — every action-needed DRAFTED message.
    if not _should_post_slack_review(state):
        if state.draft_status == "SKIPPED":
            reason = "triage skipped draft (spam or no action items)"
        elif state.draft_status == "REQUIRES_HUMAN":
            reason = "draft_status=REQUIRES_HUMAN"
        else:
            reason = f"draft_status={state.draft_status}"
        record["slack_skip_reason"] = reason
        report_slack_stage(posted=False, skipped_reason=reason, message_ts=None)
        return record

    if not _slack_configured(live_settings):
        reason = "Slack env vars not set (SLACK_BOT_TOKEN / SIGNING_SECRET / REVIEW_CHANNEL_ID)"
        record["slack_skip_reason"] = reason
        report_slack_stage(posted=False, skipped_reason=reason, message_ts=None)
        return record

    from slack_sdk.errors import SlackApiError

    from app.core.exceptions import AuditError
    from app.services import audit_service, slack_service

    slack_app = get_slack_app(live_settings)
    try:
        post_result = await slack_service.post_review_card(
            state,
            redis=redis,
            slack_app=slack_app,
            settings=live_settings,
        )
    except SlackApiError as exc:
        err = ""
        if getattr(exc, "response", None) is not None:
            data = getattr(exc.response, "data", None) or {}
            if isinstance(data, dict):
                err = str(data.get("error") or "")
        channel = live_settings.slack_review_channel_id.strip()
        if err == "not_in_channel":
            pytest.fail(
                f"Slack bot is not in channel {channel!r} (error=not_in_channel). "
                f"In Slack, open that channel and run: /invite @<your-bot-name> "
                f"(or add the app via channel integrations). Then re-run."
            )
        pytest.fail(f"Slack chat.postMessage failed for channel {channel!r}: {err or exc}")
    if post_result.status != "posted" or not post_result.message_ts:
        reason = (
            f"Slack post not completed (status={post_result.status})"
            if post_result.status != "posted"
            else "Slack post skipped (idempotent lock or API returned no ts)"
        )
        record["slack_skip_reason"] = reason
        report_slack_stage(posted=False, skipped_reason=reason, message_ts=None)
        return record

    message_ts = post_result.message_ts

    try:
        async with session_factory() as session, session.begin():
            await audit_service.log_event(
                session,
                event_type="slack.card_posted",
                conversation_id=state.original_email.conversation_id,
                mailbox=state.original_email.mailbox,
                payload={
                    "message_id": state.original_email.message_id,
                    "message_ts": message_ts,
                    "draft_status": state.draft_status,
                },
                actor="system",
            )
    except AuditError:
        pass

    record["slack_posted"] = True
    record["slack_message_ts"] = message_ts
    report_slack_stage(posted=True, skipped_reason=None, message_ts=message_ts)
    return record


@pytest.mark.asyncio
async def test_live_e2e_graph_ingest_redis_db_pii_haiku_slack(
    live_settings,
    live_mailboxes: list[str],
) -> None:
    """Full path across TEST_MAILBOXES: Graph → DB → triage → draft → Slack."""
    limit = max_messages(3)  # default: 3 per mailbox so all inboxes get hit
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
    anthropic: AsyncAnthropic | None = None
    json_path = _json_output_path()
    message_results: list[dict[str, Any]] = []
    slack_posted_count = 0
    run_payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mailboxes": live_mailboxes,
        "lookback_days": days,
        "max_messages": limit,
        "model": live_settings.classification_model,
        "prompt_version": PROMPT_VERSION,
        "slack_configured": _slack_configured(live_settings),
        "messages": message_results,
    }

    try:
        assert await redis.ping() is True
        async with session_factory() as session:
            await session.execute(select(1))

        report_run_header(
            mailboxes=live_mailboxes,
            lookback_days=days,
            max_messages=limit,
            model=live_settings.classification_model,
            redis_url=live_settings.redis_url,
            database_hint=f"...{live_settings.database_url[-40:]}",
            anthropic_configured=bool(live_settings.anthropic_api_key.strip()),
            slack_configured=_slack_configured(live_settings),
        )

        if live_settings.anthropic_api_key.strip():
            anthropic = anthropic_client_from_settings(live_settings)

        any_inbox = False
        access_denied: list[str] = []
        for mailbox in live_mailboxes:
            try:
                inbox = await _fetch_inbox(
                    graph,
                    mailbox,
                    filter_query=filter_query,
                    limit=limit,
                )
            except GraphClientError as exc:
                if _is_graph_mailbox_access_denied(exc):
                    access_denied.append(mailbox)
                    print(
                        f"\n  SKIP {mailbox}: Graph 403 AccessDenied "
                        f"(app lacks Mail.Read on this mailbox — remove from "
                        f"TARGET_MAILBOXES / TEST_MAILBOXES or fix Entra "
                        f"Application Access Policy)."
                    )
                    continue
                raise
            if not inbox:
                print(f"\n  No inbox messages for {mailbox} in the last {days} days — skipping.")
                continue
            any_inbox = True

            print(f"\n{'#' * 78}")
            print(f"#  MAILBOX: {mailbox}  ({len(inbox)} message(s))")
            print(f"{'#' * 78}")

            if anthropic is None and live_settings.anthropic_api_key.strip():
                anthropic = anthropic_client_from_settings(live_settings)

            for index, summary in enumerate(inbox, start=1):
                if anthropic is None:
                    # Will fail inside with a clear message after printing evidence.
                    anthropic = anthropic_client_from_settings(live_settings)
                record = await _process_one_message(
                    mailbox=mailbox,
                    summary=summary,
                    index=index,
                    total=len(inbox),
                    inbox_count=len(inbox),
                    live_settings=live_settings,
                    redis=redis,
                    graph=graph,
                    session_factory=session_factory,
                    anthropic=anthropic,
                )
                message_results.append(record)
                if record.get("slack_posted"):
                    slack_posted_count += 1

        if access_denied:
            run_payload["mailboxes_access_denied"] = access_denied
            print(f"\n  Mailboxes skipped (Graph AccessDenied): {', '.join(access_denied)}")

        if not any_inbox:
            if access_denied and len(access_denied) == len(live_mailboxes):
                pytest.fail(
                    "Graph AccessDenied for every mailbox: "
                    f"{', '.join(access_denied)}. Fix Entra Application Access "
                    "Policy / Mail.Read app permissions, or set TEST_MAILBOXES to "
                    "addresses the app can read."
                )
            pytest.skip(
                f"No inbox messages in the last {days} days for mailboxes: {live_mailboxes}"
            )

        report_finale(
            mailboxes=live_mailboxes,
            slack_posted_count=slack_posted_count,
            json_path=str(json_path),
        )
        run_payload["slack_posted_count"] = slack_posted_count
    finally:
        if message_results:
            run_payload["generated_at"] = datetime.now(UTC).isoformat()
            written = _write_live_e2e_json(run_payload, json_path)
            print(f"\n  JSON output written to: {written}")
        await graph.aclose()
        if anthropic is not None:
            await anthropic.close()
        await redis.aclose()
        await engine.dispose()
        await dispose_engine()
