"""Live complete suite — Graph → Redis → Postgres → Claude → draft → Slack → web API.

Opt-in only. Uses real credentials from ``backend/.env``. Stores structured
inputs/outputs under ``tests/artifacts/live/<run_id>/``.

Run from backend/:

    unset ANTHROPIC_API_KEY OPENAI_API_KEY SLACK_BOT_TOKEN
    set -a && source .env && set +a
    .venv/bin/alembic upgrade head
    SLACK_ENABLED=true \\
    RUN_LIVE_COMPLETE=1 \\
    LIVE_MAX_MESSAGES=2 \\
    .venv/bin/pytest tests/test_live_complete.py -vv -s
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, patch

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    anthropic_client_from_settings,
    close_openai_client,
    close_redis,
    close_slack_app,
    get_slack_app,
    openai_client_from_settings,
)
from app.core.exceptions import GraphClientError
from app.core.redis_keys import dedup_key, slack_posted_key
from app.db.session import dispose_engine
from app.graph.auth import GraphAuth
from app.graph.client import GraphClient
from app.llm.prompts import PROMPT_VERSION
from app.main import create_app
from app.models.db.audit_event import AuditEvent
from app.models.db.draft import Draft
from app.models.schemas.email_triage_state import EmailTriageState
from app.repositories import draft_repo, message_repo, thread_repo, user_repo
from app.services import (
    auth_service,
    draft_feedback_service,
    ingestion_service,
    pipeline_service,
)
from tests.live_helpers import (
    env_flag,
    lookback_days,
    lookback_filter,
    max_messages,
    require_anthropic_settings,
    resolve_e2e_mailboxes,
)

pytestmark = [
    pytest.mark.live_e2e,
    pytest.mark.live_graph,
    pytest.mark.live_claude,
]

_ARTIFACT_ROOT = Path(__file__).resolve().parent / "artifacts" / "live"
_TRIAGE_ELIGIBLE = frozenset({"ingested", "retry_triage"})
_LIVE_USER_EMAIL = "live-complete-e2e@example.com"
_LIVE_USER_PASSWORD = "LiveCompletePass1!"


class _LiveArtifacts:
    def __init__(self) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.root = _ARTIFACT_ROOT / f"run_{stamp}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.steps: list[dict[str, Any]] = []
        self._write_manifest()

    def _write_manifest(self) -> None:
        (self.root / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": self.root.name,
                    "created_at": datetime.now(UTC).isoformat(),
                    "steps": self.steps,
                },
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )

    def save(
        self,
        step: str,
        *,
        input_data: Any = None,
        output_data: Any = None,
        status: str = "ok",
    ) -> Path:
        safe = step.replace("/", "_").replace(" ", "_")
        path = self.root / f"{len(self.steps):03d}_{safe}.json"
        payload = {
            "step": step,
            "status": status,
            "saved_at": datetime.now(UTC).isoformat(),
            "input": input_data,
            "output": output_data,
        }
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        self.steps.append({"step": step, "status": status, "file": path.name})
        self._write_manifest()
        print(f"\n  [artifact] {path.name}")
        return path


def _slack_tokens_present(settings: Settings) -> bool:
    return bool(
        settings.slack_bot_token.strip()
        and settings.slack_signing_secret.strip()
        and settings.slack_review_channel_id.strip()
    )


def _should_post_slack(state: EmailTriageState) -> bool:
    triage = state.triage
    if triage is None or triage.is_spam or not triage.has_action_items:
        return False
    return state.draft_status == "DRAFTED"


async def _redis_snap(redis: Redis, mailbox: str, message_id: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for key in (dedup_key(mailbox, message_id), slack_posted_key(mailbox, message_id)):
        out[key] = await redis.get(key)
    return out


@pytest.fixture
def live_settings() -> Settings:
    if not env_flag("RUN_LIVE_COMPLETE"):
        pytest.skip("Set RUN_LIVE_COMPLETE=1 to run the live complete suite")
    try:
        settings = require_anthropic_settings()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    # Force Slack on for this live run when tokens exist (overrides .env false).
    if _slack_tokens_present(settings) and not settings.slack_enabled:
        return settings.model_copy(update={"slack_enabled": True})
    return settings


@pytest.fixture
def artifacts() -> _LiveArtifacts:
    return _LiveArtifacts()


@pytest.mark.asyncio
async def test_live_complete_end_to_end(live_settings: Settings, artifacts: _LiveArtifacts) -> None:
    """Live: Graph auth → fetch → ingest → Haiku/Sonnet → Slack → web API review."""
    mailboxes = resolve_e2e_mailboxes(live_settings)
    limit = max_messages(2)
    days = lookback_days(30)
    filter_query = lookback_filter(days=days)

    artifacts.save(
        "run_config",
        input_data={
            "mailboxes": mailboxes,
            "lookback_days": days,
            "max_messages": limit,
            "classification_model": live_settings.classification_model,
            "draft_model": live_settings.draft_model,
            "prompt_version": PROMPT_VERSION,
            "slack_tokens_present": _slack_tokens_present(live_settings),
            "slack_enabled": live_settings.slack_enabled,
            "openai_configured": bool(live_settings.openai_api_key.strip()),
        },
        output_data={"artifact_dir": str(artifacts.root)},
    )

    await dispose_engine()
    await close_redis()
    await close_slack_app()
    await close_openai_client()
    get_settings.cache_clear()

    engine = create_async_engine(live_settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    redis = Redis.from_url(
        live_settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2.0,
        socket_timeout=5.0,
    )
    auth = GraphAuth(live_settings, redis=redis)
    graph = GraphClient(auth)
    anthropic = anthropic_client_from_settings(live_settings)
    openai = openai_client_from_settings(live_settings)

    drafted_thread_ids: list[str] = []
    message_records: list[dict[str, Any]] = []
    access_denied: list[str] = []

    try:
        assert await redis.ping() is True
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))

        # --- Graph token smoke ---
        token = await auth.get_access_token()
        assert token
        artifacts.save(
            "graph_auth",
            output_data={"token_acquired": True, "token_prefix": token[:12] + "…"},
        )

        any_inbox = False
        for mailbox in mailboxes:
            try:
                if limit is None:
                    inbox = await graph.list_messages(
                        mailbox,
                        folder="inbox",
                        filter_query=filter_query,
                        top=50,
                        orderby="receivedDateTime desc",
                        follow_next_link=True,
                    )
                else:
                    inbox = await graph.list_messages(
                        mailbox,
                        folder="inbox",
                        filter_query=filter_query,
                        top=limit,
                        orderby="receivedDateTime desc",
                        follow_next_link=False,
                    )
            except GraphClientError as exc:
                err = str(exc)
                if "403" in err or "AccessDenied" in err or "Access is denied" in err:
                    access_denied.append(mailbox)
                    artifacts.save(
                        f"mailbox_access_denied_{mailbox.split('@')[0]}",
                        input_data={"mailbox": mailbox},
                        output_data={"error": err[:500]},
                        status="skipped",
                    )
                    continue
                raise

            artifacts.save(
                f"graph_inbox_{mailbox.split('@')[0]}",
                input_data={"mailbox": mailbox, "filter": filter_query, "limit": limit},
                output_data={
                    "count": len(inbox),
                    "subjects": [m.subject for m in inbox[:10]],
                },
            )
            if not inbox:
                continue
            any_inbox = True

            for index, summary in enumerate(inbox, start=1):
                assert summary.id and summary.conversation_id
                message_id = summary.id
                conversation_id = summary.conversation_id

                if not env_flag("LIVE_E2E_KEEP_DEDUP"):
                    await redis.delete(dedup_key(mailbox, message_id))
                    await redis.delete(slack_posted_key(mailbox, message_id))

                before = await _redis_snap(redis, mailbox, message_id)
                async with session_factory() as session, session.begin():
                    ingest = await ingestion_service.ingest_graph_message(
                        session=session,
                        redis=redis,
                        graph_client=graph,
                        mailbox=mailbox,
                        message_id=message_id,
                    )

                record: dict[str, Any] = {
                    "mailbox": mailbox,
                    "index": index,
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "subject": summary.subject,
                    "ingest_status": ingest.status,
                    "thread_id": ingest.thread_id,
                    "redis_before": before,
                    "triage": None,
                    "draft_status": None,
                    "draft": None,
                    "slack_posted": False,
                    "slack_skip_reason": None,
                }

                if ingest.status not in _TRIAGE_ELIGIBLE:
                    artifacts.save(
                        f"pipeline_{mailbox.split('@')[0]}_{index}_skipped",
                        input_data={"message_id": message_id, "subject": summary.subject},
                        output_data=record,
                        status="skipped",
                    )
                    message_records.append(record)
                    continue

                async with session_factory() as session, session.begin():
                    state = await pipeline_service.run_after_ingest(
                        session=session,
                        redis=redis,
                        settings=live_settings,
                        client=anthropic,
                        openai_client=openai,
                        ingest_result=ingest,
                    )

                if pipeline_service.pipeline_ready_for_dedup(state):
                    await ingestion_service.complete_ingest_dedup(redis, mailbox, message_id)
                else:
                    await ingestion_service.release_ingest_dedup(redis, mailbox, message_id)

                record["redis_after"] = await _redis_snap(redis, mailbox, message_id)
                record["draft_status"] = state.draft_status
                record["error_logs"] = state.error_logs
                if state.triage is not None:
                    record["triage"] = state.triage.model_dump(mode="json")
                if state.draft is not None:
                    record["draft"] = state.draft.model_dump(mode="json")
                    if ingest.thread_id:
                        drafted_thread_ids.append(ingest.thread_id)

                assert state.triage is not None, f"triage failed: {state.error_logs}"
                assert state.draft_status in {"SKIPPED", "DRAFTED", "REQUIRES_HUMAN"}

                # Slack review card (live)
                if _should_post_slack(state) and _slack_tokens_present(live_settings):
                    from slack_sdk.errors import SlackApiError

                    from app.services import audit_service, slack_service

                    await close_slack_app()
                    slack_app = get_slack_app(live_settings)
                    assert slack_app is not None, "Slack tokens present but app not built"
                    try:
                        post = await slack_service.post_review_card(
                            state,
                            redis=redis,
                            slack_app=slack_app,
                            settings=live_settings,
                        )
                    except SlackApiError as exc:
                        record["slack_skip_reason"] = f"SlackApiError: {exc}"
                    else:
                        if post.status == "posted" and post.message_ts:
                            record["slack_posted"] = True
                            record["slack_message_ts"] = post.message_ts
                            try:
                                async with session_factory() as session, session.begin():
                                    await audit_service.log_event(
                                        session,
                                        event_type="slack.card_posted",
                                        conversation_id=state.original_email.conversation_id,
                                        mailbox=state.original_email.mailbox,
                                        payload={
                                            "message_id": state.original_email.message_id,
                                            "message_ts": post.message_ts,
                                            "draft_status": state.draft_status,
                                        },
                                        actor="live-complete",
                                    )
                            except Exception:  # noqa: BLE001 — best-effort audit
                                pass
                        else:
                            record["slack_skip_reason"] = f"status={post.status}"
                elif _should_post_slack(state):
                    record["slack_skip_reason"] = "Slack tokens not configured"
                else:
                    record["slack_skip_reason"] = f"not eligible (draft_status={state.draft_status})"

                artifacts.save(
                    f"pipeline_{mailbox.split('@')[0]}_{index}",
                    input_data={
                        "mailbox": mailbox,
                        "message_id": message_id,
                        "subject": summary.subject,
                    },
                    output_data=record,
                )
                message_records.append(record)

        if access_denied and not any_inbox:
            pytest.fail(f"Graph AccessDenied for all mailboxes: {access_denied}")
        if not any_inbox:
            pytest.skip(f"No inbox messages in last {days} days for {mailboxes}")

        # --- Live web API against real DB (seed ephemeral reviewer) ---
        async with session_factory() as session:
            existing = await user_repo.get_by_email(session, _LIVE_USER_EMAIL)
        if existing is None:
            async with session_factory() as session, session.begin():
                await auth_service.create_user(
                    session,
                    email=_LIVE_USER_EMAIL,
                    password=_LIVE_USER_PASSWORD,
                    role="admin",
                )

        get_settings.cache_clear()
        with (
            patch("app.main.get_settings", return_value=live_settings),
            patch("app.core.config.get_settings", return_value=live_settings),
            patch("app.db.session.get_settings", return_value=live_settings),
            patch("app.core.dependencies.get_settings", return_value=live_settings),
            patch("app.main.run_subscription_reconcile", new=AsyncMock()),
            patch("app.main.AsyncIOScheduler") as sched,
        ):
            sched.return_value.start = lambda: None
            sched.return_value.shutdown = lambda wait=False: None
            # Use real Slack resolution from settings.
            await close_slack_app()
            application = create_app()
            application.dependency_overrides[get_settings] = lambda: live_settings

            transport = ASGITransport(app=application)
            async with AsyncClient(transport=transport, base_url="http://live.test") as client:
                health = await client.get("/health")
                artifacts.save(
                    "api_health",
                    output_data={"status_code": health.status_code, "body": health.json()},
                )
                assert health.status_code == 200

                login = await client.post(
                    "/auth/login",
                    json={"email": _LIVE_USER_EMAIL, "password": _LIVE_USER_PASSWORD},
                    headers={"Origin": live_settings.frontend_origin},
                )
                artifacts.save(
                    "api_login",
                    input_data={"email": _LIVE_USER_EMAIL},
                    output_data={
                        "status_code": login.status_code,
                        "user": (login.json().get("user") if login.status_code == 200 else login.text),
                    },
                )
                assert login.status_code == 200, login.text
                headers = {
                    "Authorization": f"Bearer {login.json()['access_token']}",
                    "Origin": live_settings.frontend_origin,
                }

                me = await client.get("/auth/me", headers=headers)
                overview = await client.get("/api/dashboard/overview", headers=headers)
                mailboxes_resp = await client.get("/api/mailboxes", headers=headers)
                skills = await client.get("/api/skills", headers=headers)
                reply_memory = await client.get("/api/reply-memory", headers=headers)
                tones = await client.get("/api/tone-profiles", headers=headers)
                candidates = await client.get("/api/skill-candidates", headers=headers)

                artifacts.save(
                    "api_dashboard_and_settings",
                    output_data={
                        "me": me.json() if me.status_code == 200 else me.text,
                        "overview_status": overview.status_code,
                        "overview": overview.json() if overview.status_code == 200 else overview.text,
                        "mailboxes_status": mailboxes_resp.status_code,
                        "mailboxes": mailboxes_resp.json()
                        if mailboxes_resp.status_code == 200
                        else mailboxes_resp.text,
                        "skills": skills.json() if skills.status_code == 200 else skills.text,
                        "reply_memory_count": len(reply_memory.json())
                        if reply_memory.status_code == 200
                        else reply_memory.status_code,
                        "tone_profiles_count": len(tones.json())
                        if tones.status_code == 200
                        else tones.status_code,
                        "skill_candidates_count": len(candidates.json())
                        if candidates.status_code == 200
                        else candidates.status_code,
                    },
                )
                assert me.status_code == 200
                assert overview.status_code == 200
                assert mailboxes_resp.status_code == 200
                assert skills.status_code == 200

                # Open first drafted thread from this run (or any existing draft)
                target_thread_id = drafted_thread_ids[0] if drafted_thread_ids else None
                if target_thread_id is None:
                    async with session_factory() as session:
                        stmt = (
                            select(Draft)
                            .where(Draft.approved_at.is_(None), Draft.rejected_at.is_(None))
                            .order_by(Draft.created_at.desc())
                            .limit(1)
                        )
                        row = (await session.execute(stmt)).scalar_one_or_none()
                        if row is not None:
                            target_thread_id = str(row.thread_id)

                if target_thread_id:
                    detail = await client.get(
                        f"/api/threads/{target_thread_id}",
                        headers=headers,
                    )
                    artifacts.save(
                        "api_thread_detail",
                        input_data={"thread_id": target_thread_id},
                        output_data={
                            "status_code": detail.status_code,
                            "body": detail.json() if detail.status_code == 200 else detail.text,
                        },
                    )
                    assert detail.status_code == 200
                    draft_view = detail.json().get("draft")
                    if draft_view and draft_view.get("id") and not draft_view.get("feedback_action"):
                        draft_id = draft_view["id"]
                        # Live approve path (never sends email) — skip OpenAI side effects noise
                        # by allowing real reply-memory if OpenAI is configured.
                        approve = await client.post(
                            f"/api/drafts/{draft_id}/approve",
                            json={},
                            headers=headers,
                        )
                        artifacts.save(
                            "api_draft_approve",
                            input_data={"draft_id": draft_id},
                            output_data={
                                "status_code": approve.status_code,
                                "body": approve.json()
                                if approve.status_code == 200
                                else approve.text,
                            },
                        )
                        assert approve.status_code == 200
                        assert approve.json()["feedback_action"] == "approve"

                        # Live regenerate on a fresh drafted thread if another exists
                        regen_thread = None
                        for tid in drafted_thread_ids[1:]:
                            regen_thread = tid
                            break
                        if regen_thread:
                            regen = await client.post(
                                f"/api/threads/{regen_thread}/regenerate-draft",
                                json={"instruction": "Make the reply shorter and more formal."},
                                headers=headers,
                            )
                            artifacts.save(
                                "api_draft_regenerate",
                                input_data={
                                    "thread_id": regen_thread,
                                    "instruction": "Make the reply shorter and more formal.",
                                },
                                output_data={
                                    "status_code": regen.status_code,
                                    "body": regen.json()
                                    if regen.status_code in {200, 201}
                                    else regen.text,
                                },
                            )
                            assert regen.status_code == 201

            application.dependency_overrides.clear()

        artifacts.save(
            "run_complete",
            output_data={
                "messages_processed": len(message_records),
                "drafted_thread_ids": drafted_thread_ids,
                "slack_posted": sum(1 for r in message_records if r.get("slack_posted")),
                "access_denied": access_denied,
                "artifact_dir": str(artifacts.root),
            },
        )
        print(f"\n  Live complete artifacts: {artifacts.root}")
    finally:
        await graph.aclose()
        await anthropic.close()
        if openai is not None:
            await openai.close()
        await redis.aclose()
        await engine.dispose()
        await dispose_engine()
        await close_redis()
        await close_slack_app()
        await close_openai_client()
        get_settings.cache_clear()
