"""HTTP contract for POST /api/threads/{id}/not-spam."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_anthropic_client, get_db, get_openai_client, get_redis
from app.core.dependencies_auth import get_current_user
from app.core.exceptions import ThreadStateError
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.models.schemas.spam import NotSpamResponseSchema

THREAD_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1")


@pytest.fixture
def local_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes="elise@sample-site.example.com",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


@pytest.fixture
def app(local_settings: Settings):
    get_settings.cache_clear()
    with (
        patch("app.main.get_settings", return_value=local_settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        application = create_app()
        application.dependency_overrides[get_settings] = lambda: local_settings

        async def fake_user() -> UserMe:
            return UserMe(
                id=uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
                email="elise@sample-site.example.com",
                role="user",
                created_at=datetime.now(UTC),
            )

        application.dependency_overrides[get_current_user] = fake_user
        application.dependency_overrides[get_db] = lambda: AsyncMock(
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )
        application.dependency_overrides[get_redis] = lambda: AsyncMock()
        application.dependency_overrides[get_anthropic_client] = lambda: AsyncMock()
        application.dependency_overrides[get_openai_client] = lambda: None
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_not_spam_requires_auth(local_settings: Settings) -> None:
    get_settings.cache_clear()
    with (
        patch("app.main.get_settings", return_value=local_settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        application = create_app()
        application.dependency_overrides[get_settings] = lambda: local_settings
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            resp = await client.post(f"/api/threads/{THREAD_ID}/not-spam")
        application.dependency_overrides.clear()
    assert resp.status_code == 401
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_not_spam_returns_unhidden_thread_and_outlook_unchanged(app) -> None:
    payload = NotSpamResponseSchema(
        thread_id=THREAD_ID,
        state="DRAFTED",
        is_spam=False,
        sender_address="orders@sample-lab.example.com",
        outlook_unchanged=True,
    )
    with patch(
        "app.api.web.threads.not_spam_service.mark_not_spam",
        AsyncMock(return_value=payload),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(f"/api/threads/{THREAD_ID}/not-spam")

    assert resp.status_code == 200
    body = resp.json()
    assert body["thread_id"] == str(THREAD_ID)
    assert body["is_spam"] is False
    assert body["state"] == "DRAFTED"
    assert body["sender_address"] == "orders@sample-lab.example.com"
    assert body["outlook_unchanged"] is True


@pytest.mark.asyncio
async def test_not_spam_on_non_spam_thread_is_conflict(app) -> None:
    with patch(
        "app.api.web.threads.not_spam_service.mark_not_spam",
        AsyncMock(side_effect=ThreadStateError("Thread is not SPAM")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(f"/api/threads/{THREAD_ID}/not-spam")

    assert resp.status_code == 409
