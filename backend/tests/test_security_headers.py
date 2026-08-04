"""Security headers present on responses."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def app_with_local_settings():
    settings = Settings(
        environment="local",
        jwt_secret="a" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )
    get_settings.cache_clear()
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: settings
        yield app, settings
        app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_security_headers_on_health(app_with_local_settings) -> None:
    """Health response includes the security header suite."""
    app, _settings = app_with_local_settings
    with (
        patch("app.main.get_redis", AsyncMock(return_value=AsyncMock(ping=AsyncMock()))),
        patch("app.main.get_session_factory") as factory,
    ):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        session.execute = AsyncMock()
        factory.return_value = lambda: session

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=()" in resp.headers["Permissions-Policy"]
    assert resp.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert resp.headers["Cross-Origin-Resource-Policy"] == "cross-origin"
    assert "default-src 'none'" in resp.headers["Content-Security-Policy"]
