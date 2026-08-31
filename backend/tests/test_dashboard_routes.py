"""Dashboard route auth and 404 behavior."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.security.tokens import create_access_token
from app.main import create_app
from app.models.schemas.dashboard import DashboardOverview, ThreadList


@pytest.mark.asyncio
async def test_overview_requires_auth(local_settings: Settings) -> None:
    """Unauthenticated overview returns 401."""
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
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/dashboard/overview")
        application.dependency_overrides.clear()
    assert resp.status_code == 401
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_overview_ok(app, local_settings: Settings) -> None:
    overview = DashboardOverview(
        mailboxes=[],
        total_threads=0,
        total_awaiting=0,
        total_stale=0,
        recent_activity=[],
        updated_at=datetime.now(UTC),
    )
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch(
        "app.api.web.dashboard.dashboard_service.get_overview",
        AsyncMock(return_value=overview),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/dashboard/overview")
    assert resp.status_code == 200
    assert resp.json()["total_threads"] == 0


@pytest.mark.asyncio
async def test_unknown_mailbox_404(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/mailboxes/not-a-real-box/threads")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mailbox_threads_ok(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch(
        "app.api.web.mailboxes.mailbox_service.list_threads",
        AsyncMock(return_value=ThreadList(items=[], next_cursor=None)),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/mailboxes/sales/threads")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_mailbox_threads_rejects_inverted_date_range(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/mailboxes/sales/threads",
            params={"from": "2026-08-29", "to": "2026-08-01"},
        )
    assert resp.status_code == 400
    assert "from must be on or before to" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_access_token_works_without_override(local_settings: Settings) -> None:
    """Real JWT dependency accepts a valid access token."""
    from app.repositories.user_repo import UserRead

    get_settings.cache_clear()
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    token, _ = create_access_token(user_id, local_settings)
    user = UserRead(
        id=user_id,
        email="elise@example.com",
        password_hash="x",
        password_updated_at=now,
        token_version=0,
        role="user",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    with (
        patch("app.main.get_settings", return_value=local_settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
        patch(
            "app.core.dependencies_auth.user_repo.get_by_id",
            AsyncMock(return_value=user),
        ),
        patch(
            "app.api.web.dashboard.dashboard_service.get_overview",
            AsyncMock(
                return_value=DashboardOverview(
                    mailboxes=[],
                    total_threads=0,
                    total_awaiting=0,
                    total_stale=0,
                    recent_activity=[],
                    updated_at=now,
                )
            ),
        ),
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        application = create_app()
        application.dependency_overrides[get_settings] = lambda: local_settings

        async def fake_db():
            yield AsyncMock()

        application.dependency_overrides[get_db] = fake_db
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/dashboard/overview",
                headers={"Authorization": f"Bearer {token}"},
            )
        application.dependency_overrides.clear()
    assert resp.status_code == 200
    get_settings.cache_clear()
