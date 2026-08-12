"""Ops metrics preview API auth and validation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_user
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.models.schemas.ops_report import OpsMetricsResponse, OpsPeriod


@pytest.fixture
def local_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes="sales@example.com,cr@example.com",
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
                id=uuid.uuid4(),
                email="elise@example.com",
                role="user",
                created_at=datetime.now(UTC),
            )

        application.dependency_overrides[get_current_user] = fake_user
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


def _metrics() -> OpsMetricsResponse:
    start = datetime(2026, 8, 3, tzinfo=UTC)
    end = datetime(2026, 8, 9, tzinfo=UTC)
    return OpsMetricsResponse(
        period=OpsPeriod(date_from=start, date_to=end),
        volume_by_mailbox=[],
        total_volume=0,
        spam_filtered=0,
        approvals=0,
        rejects=0,
        approval_rate=0.0,
        top_reject_themes=[],
        avg_resolve_hours=None,
        resolve_sample_count=0,
        generated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_ops_metrics_requires_auth(local_settings: Settings) -> None:
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
            resp = await client.get("/api/reports/ops-metrics")
        application.dependency_overrides.clear()
    assert resp.status_code == 401
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ops_metrics_date_only_uses_ny_calendar_days(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch(
        "app.api.web.reports.ops_metrics_service.get_metrics",
        AsyncMock(return_value=_metrics()),
    ) as get_metrics:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/reports/ops-metrics",
                params={"from": "2026-08-03", "to": "2026-08-09"},
            )
    assert resp.status_code == 200
    start = get_metrics.await_args.args[2]
    end = get_metrics.await_args.args[3]
    ny = ZoneInfo("America/New_York")
    assert start.astimezone(ny).date().isoformat() == "2026-08-03"
    assert start.astimezone(ny).hour == 0
    assert end.astimezone(ny).date().isoformat() == "2026-08-09"
    assert end.astimezone(ny).hour == 23


@pytest.mark.asyncio
async def test_ops_metrics_ok(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch(
        "app.api.web.reports.ops_metrics_service.get_metrics",
        AsyncMock(return_value=_metrics()),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/reports/ops-metrics",
                params={"from": "2026-08-03T00:00:00Z", "to": "2026-08-09T23:59:59Z"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_volume"] == 0
    assert "from" in body["period"]
    assert "to" in body["period"]
    assert body["approval_rate"] == 0.0


@pytest.mark.asyncio
async def test_ops_metrics_inverted_range_422(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/reports/ops-metrics",
            params={"from": "2026-08-09T00:00:00Z", "to": "2026-08-03T00:00:00Z"},
        )
    assert resp.status_code == 422
    assert resp.json()["error_type"] == "InvalidDateRangeError"


@pytest.mark.asyncio
async def test_ops_metrics_unknown_mailbox_404(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/reports/ops-metrics",
            params={
                "from": "2026-08-03T00:00:00Z",
                "to": "2026-08-09T00:00:00Z",
                "mailbox": "unknown@example.com",
            },
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ops_metrics_partial_range_422(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/reports/ops-metrics",
            params={"from": "2026-08-03T00:00:00Z"},
        )
    assert resp.status_code == 422
