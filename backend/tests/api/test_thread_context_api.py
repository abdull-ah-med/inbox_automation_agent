"""HTTP contract for /api/threads/{id}/context.

Oracles: PUT 200 with the pin literal; stale expected_version is 409;
rebuild is 202; flag off is 404.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    get_anthropic_client,
    get_db,
    get_graph_client,
    get_openai_client,
    get_redis,
)
from app.core.dependencies_auth import get_current_user
from app.main import create_app
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.auth import UserMe
from app.repositories import thread_context_repo

pytestmark = pytest.mark.db

MAILBOX = "elise@sample-site.example.com"
PIN_TEXT = "Do not CC legal"
FACT_BODY = "Check 11111 is for driver Ames"


def _settings(*, enabled: bool) -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        thread_context_enabled=enabled,
    )


async def _seed_thread(session) -> Thread:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id=str(uuid.uuid4()),
        subject="Cancel check",
        state="NEW",
        last_message_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    session.add(thread)
    await session.flush()
    session.add(
        Message(
            id=uuid.uuid4(),
            thread_id=thread.id,
            graph_message_id=str(uuid.uuid4()),
            direction="inbound",
            sender="buyer@acme.com",
            body_text="Please cancel",
            received_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
            to_recipients=[MAILBOX],
        )
    )
    await session.commit()
    return thread


async def _client(settings: Settings, db_session):
    get_settings.cache_clear()
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: settings

    async def fake_user() -> UserMe:
        return UserMe(
            id=uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            email=MAILBOX,
            role="user",
            created_at=datetime.now(UTC),
        )

    application.dependency_overrides[get_current_user] = fake_user
    application.dependency_overrides[get_db] = lambda: db_session
    application.dependency_overrides[get_redis] = lambda: None
    application.dependency_overrides[get_anthropic_client] = lambda: None
    application.dependency_overrides[get_openai_client] = lambda: None
    application.dependency_overrides[get_graph_client] = lambda: None
    return application


async def test_get_context_returns_pins_and_active_facts(db_session) -> None:
    thread = await _seed_thread(db_session)
    message_id = (
        await db_session.execute(
            __import__("sqlalchemy").select(Message.id).where(Message.thread_id == thread.id)
        )
    ).scalar_one()
    await thread_context_repo.get_or_create(db_session, thread.id)
    await thread_context_repo.save_user_notes(
        db_session, thread.id, notes=PIN_TEXT, expected_version=0
    )
    await thread_context_repo.add_facts(
        db_session,
        thread.id,
        [{"body": FACT_BODY, "source_message_id": message_id, "actor_kind": "llm"}],
    )
    await db_session.commit()

    application = await _client(_settings(enabled=True), db_session)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        resp = await client.get(f"/api/threads/{thread.id}/context")
    application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_notes"] == PIN_TEXT
    assert body["version"] == 1
    assert [fact["body"] for fact in body["facts"]] == [FACT_BODY]


async def test_get_context_without_pointer_does_not_insert(db_session) -> None:
    from sqlalchemy import func, select

    from app.models.db.thread_context import ThreadContext

    thread = await _seed_thread(db_session)
    application = await _client(_settings(enabled=True), db_session)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        resp = await client.get(f"/api/threads/{thread.id}/context")
    application.dependency_overrides.clear()
    get_settings.cache_clear()

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(ThreadContext)
            .where(ThreadContext.thread_id == thread.id)
        )
    ).scalar_one()
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 0
    assert resp.json()["user_notes"] == ""
    assert resp.json()["facts"] == []
    assert resp.json()["needs_initial_extract"] is True
    assert count == 0


async def test_put_user_notes_stale_version_is_409(db_session) -> None:
    thread = await _seed_thread(db_session)
    await thread_context_repo.get_or_create(db_session, thread.id)
    await db_session.commit()

    application = await _client(_settings(enabled=True), db_session)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        first = await client.put(
            f"/api/threads/{thread.id}/context/user_notes",
            json={"user_notes": PIN_TEXT, "expected_version": 0},
        )
        stale = await client.put(
            f"/api/threads/{thread.id}/context/user_notes",
            json={"user_notes": "overwrite", "expected_version": 0},
        )
    application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert first.status_code == 200, first.text
    assert first.json()["user_notes"] == PIN_TEXT
    assert first.json()["version"] == 1
    assert stale.status_code == 409


async def test_get_context_hides_signature_and_rewrites_elises_email(db_session) -> None:
    thread = await _seed_thread(db_session)
    message_id = (
        await db_session.execute(
            __import__("sqlalchemy").select(Message.id).where(Message.thread_id == thread.id)
        )
    ).scalar_one()
    outbound = Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="outbound",
        sender=MAILBOX,
        sender_name="Elise Chouest",
        body_text="Checking those ids",
        received_at=datetime(2026, 9, 4, 12, 5, tzinfo=UTC),
        to_recipients=["buyer@acme.com"],
    )
    db_session.add(outbound)
    await thread_context_repo.get_or_create(db_session, thread.id)
    await thread_context_repo.add_facts(
        db_session,
        thread.id,
        [
            {
                "body": (
                    "Elise Chouest is Director at SampleSite with email "
                    "sampleagent@sample-site.example.com and phone (202) 555-0105 ext. 201."
                ),
                "source_message_id": message_id,
                "actor_kind": "llm",
            },
            {
                "body": (
                    "sampleagent@sample-site.example.com asked Dev whether they have pre-QBO data."
                ),
                "source_message_id": outbound.id,
                "actor_kind": "llm",
            },
        ],
    )
    await db_session.commit()

    application = await _client(_settings(enabled=True), db_session)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        resp = await client.get(f"/api/threads/{thread.id}/context")
    application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert resp.status_code == 200, resp.text
    bodies = [fact["body"] for fact in resp.json()["facts"]]
    assert bodies == ["Elise asked Dev whether they have pre-QBO data."]


async def test_discard_fact_omits_it_from_get(db_session) -> None:
    thread = await _seed_thread(db_session)
    message_id = (
        await db_session.execute(
            __import__("sqlalchemy").select(Message.id).where(Message.thread_id == thread.id)
        )
    ).scalar_one()
    await thread_context_repo.get_or_create(db_session, thread.id)
    rows = await thread_context_repo.add_facts(
        db_session,
        thread.id,
        [{"body": FACT_BODY, "source_message_id": message_id, "actor_kind": "llm"}],
    )
    await db_session.commit()

    application = await _client(_settings(enabled=True), db_session)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        discarded = await client.delete(f"/api/threads/{thread.id}/context/facts/{rows[0].id}")
        after = await client.get(f"/api/threads/{thread.id}/context")
    application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["facts"] == []
    assert after.json()["facts"] == []


async def test_rebuild_returns_context_payload(db_session) -> None:
    thread = await _seed_thread(db_session)
    application = await _client(_settings(enabled=True), db_session)
    with patch("app.api.web.threads._run_context_rebuild", new_callable=AsyncMock):
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            resp = await client.post(f"/api/threads/{thread.id}/context/rebuild")
    application.dependency_overrides.clear()
    get_settings.cache_clear()
    assert resp.status_code == 202, resp.text
    assert resp.json()["user_notes"] == ""
    assert resp.json()["rebuild_in_progress"] is True


async def test_rebuild_returns_202_without_waiting_for_haiku(db_session) -> None:
    """POST must not block on Haiku — a hung client on the request dep is ignored."""
    hang = asyncio.Event()
    stuck = AsyncMock()

    async def _never(**_kwargs):
        await hang.wait()
        raise AssertionError("request-path Haiku must not run")

    stuck.messages.create = AsyncMock(side_effect=_never)

    thread = await _seed_thread(db_session)
    application = await _client(_settings(enabled=True), db_session)
    application.dependency_overrides[get_anthropic_client] = lambda: stuck
    started = time.monotonic()
    try:
        with patch("app.api.web.threads._run_context_rebuild", new_callable=AsyncMock):
            async with AsyncClient(
                transport=ASGITransport(app=application),
                base_url="http://test",
                timeout=2.0,
            ) as client:
                resp = await client.post(f"/api/threads/{thread.id}/context/rebuild")
    finally:
        hang.set()
        application.dependency_overrides.clear()
        get_settings.cache_clear()

    elapsed = time.monotonic() - started
    assert resp.status_code == 202, resp.text
    assert resp.json()["rebuild_in_progress"] is True
    assert elapsed < 2.0
    assert stuck.messages.create.await_count == 0


async def test_second_rebuild_while_running_still_in_progress(db_session) -> None:
    """A second click while extract_status=running must not 500 or drop the flag."""
    thread = await _seed_thread(db_session)
    await thread_context_repo.get_or_create(db_session, thread.id)
    await thread_context_repo.set_extract_status(
        db_session,
        thread.id,
        status="running",
        started_at=datetime.now(UTC),
        error=None,
    )
    await db_session.commit()

    application = await _client(_settings(enabled=True), db_session)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        resp = await client.post(f"/api/threads/{thread.id}/context/rebuild")
        polled = await client.get(f"/api/threads/{thread.id}/context")
    application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert resp.status_code == 202, resp.text
    assert resp.json()["rebuild_in_progress"] is True
    assert polled.status_code == 200, polled.text
    assert polled.json()["rebuild_in_progress"] is True


async def test_get_context_rebuild_in_progress_from_extract_status(db_session) -> None:
    thread = await _seed_thread(db_session)
    await thread_context_repo.get_or_create(db_session, thread.id)
    await thread_context_repo.set_extract_status(
        db_session,
        thread.id,
        status="running",
        started_at=datetime.now(UTC),
        error=None,
    )
    await db_session.commit()

    application = await _client(_settings(enabled=True), db_session)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        resp = await client.get(f"/api/threads/{thread.id}/context")
    application.dependency_overrides.clear()
    get_settings.cache_clear()
    assert resp.status_code == 200, resp.text
    assert resp.json()["rebuild_in_progress"] is True


async def test_context_routes_404_when_flag_off(db_session) -> None:
    thread = await _seed_thread(db_session)
    application = await _client(_settings(enabled=False), db_session)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        get_resp = await client.get(f"/api/threads/{thread.id}/context")
        put_resp = await client.put(
            f"/api/threads/{thread.id}/context/user_notes",
            json={"user_notes": PIN_TEXT, "expected_version": 0},
        )
        rebuild = await client.post(f"/api/threads/{thread.id}/context/rebuild")
        discard = await client.delete(f"/api/threads/{thread.id}/context/facts/{uuid.uuid4()}")
    application.dependency_overrides.clear()
    get_settings.cache_clear()
    assert get_resp.status_code == 404
    assert put_resp.status_code == 404
    assert rebuild.status_code == 404
    assert discard.status_code == 404
