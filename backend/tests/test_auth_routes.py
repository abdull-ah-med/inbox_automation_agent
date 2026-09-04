"""Auth route integration tests with mocked auth_service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_redis
from app.core.security.csrf import mint_csrf
from app.main import create_app
from app.models.schemas.auth import TokenResponse, UserMe
from app.services.auth_service import AuthResult


@pytest.fixture
def local_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="b" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        refresh_cookie_name="itr_refresh",
        csrf_cookie_name="itr_csrf",
        csrf_header_name="X-CSRF-Token",
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
        application.dependency_overrides[get_redis] = lambda: AsyncMock()
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


def _origin_headers(settings: Settings) -> dict[str, str]:
    return {"Origin": settings.frontend_origin}


def _token_result() -> AuthResult:
    user = UserMe(
        id=uuid.uuid4(),
        email="elise@example.com",
        role="user",
        created_at=datetime.now(UTC),
    )
    return AuthResult(
        response=TokenResponse(
            access_token="access.jwt.token",
            expires_in=900,
            user=user,
        ),
        refresh_plaintext="opaque-refresh-token-value",
    )


@pytest.mark.asyncio
async def test_login_sets_cookies(app, local_settings: Settings) -> None:
    """Login returns access token and sets HttpOnly refresh + CSRF cookies."""
    result = _token_result()
    mock_session = AsyncMock()
    mock_session.begin = lambda: AsyncMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=None),
    )

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch("app.api.auth.routes.auth_service.login", AsyncMock(return_value=result)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/auth/login",
                json={"email": "elise@example.com", "password": "CorrectHorseBattery1!"},
                headers=_origin_headers(local_settings),
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == "access.jwt.token"
    assert body["token_type"] == "Bearer"
    refresh = resp.cookies.get(local_settings.refresh_cookie_name)
    assert refresh == "opaque-refresh-token-value"
    assert local_settings.csrf_cookie_name in resp.cookies


@pytest.mark.asyncio
async def test_refresh_requires_csrf(app, local_settings: Settings) -> None:
    """Refresh without CSRF header is forbidden."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(local_settings.refresh_cookie_name, "opaque", path="/auth")
        resp = await client.post(
            "/auth/refresh",
            headers=_origin_headers(local_settings),
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_refresh_with_csrf(app, local_settings: Settings) -> None:
    """Refresh with matching CSRF cookie/header succeeds."""
    result = _token_result()
    csrf = mint_csrf(local_settings.jwt_secret)
    mock_session = AsyncMock()
    mock_session.begin = lambda: AsyncMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=None),
    )

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch("app.api.auth.routes.auth_service.refresh", AsyncMock(return_value=result)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set(local_settings.refresh_cookie_name, "opaque", path="/auth")
            client.cookies.set(local_settings.csrf_cookie_name, csrf, path="/")
            resp = await client.post(
                "/auth/refresh",
                headers={
                    **_origin_headers(local_settings),
                    local_settings.csrf_header_name: csrf,
                },
            )
    assert resp.status_code == 200
    assert resp.json()["access_token"] == "access.jwt.token"


@pytest.mark.asyncio
async def test_refresh_grace_failure_still_sets_cookies(app, local_settings: Settings) -> None:
    """Redis grace failure after rotation still returns 200 and Set-Cookie."""
    result = AuthResult(
        response=_token_result().response,
        refresh_plaintext="opaque-refresh-token-value",
        rotated_from_hash="old-refresh-hash",
    )
    csrf = mint_csrf(local_settings.jwt_secret)
    mock_session = AsyncMock()
    mock_session.begin = lambda: AsyncMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=None),
    )

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with (
        patch("app.api.auth.routes.auth_service.refresh", AsyncMock(return_value=result)),
        patch(
            "app.api.auth.routes.auth_service.store_refresh_grace",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set(local_settings.refresh_cookie_name, "opaque", path="/auth")
            client.cookies.set(local_settings.csrf_cookie_name, csrf, path="/")
            resp = await client.post(
                "/auth/refresh",
                headers={
                    **_origin_headers(local_settings),
                    local_settings.csrf_header_name: csrf,
                },
            )
    assert resp.status_code == 200
    assert resp.json()["access_token"] == "access.jwt.token"
    set_cookie = " ".join(
        v.decode() if isinstance(v, bytes) else str(v)
        for k, v in resp.headers.multi_items()
        if k.lower() == "set-cookie"
    )
    assert local_settings.refresh_cookie_name in set_cookie.lower()


@pytest.mark.asyncio
async def test_login_records_nginx_x_real_ip_when_proxy_trusted() -> None:
    """Behind nginx, persist X-Real-IP ($remote_addr), not the Uvicorn peer.

    https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header
    https://fastapi.tiangolo.com/advanced/behind-a-proxy/
    """
    settings = Settings(
        environment="local",
        jwt_secret="b" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        trust_x_forwarded_for=True,
        refresh_cookie_name="itr_refresh",
        csrf_cookie_name="itr_csrf",
        csrf_header_name="X-CSRF-Token",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )
    result = _token_result()
    mock_session = AsyncMock()
    mock_session.begin = lambda: AsyncMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=None),
    )

    async def fake_db():
        yield mock_session

    get_settings.cache_clear()
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
        patch(
            "app.api.auth.routes.auth_service.login",
            AsyncMock(return_value=result),
        ) as login_mock,
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        application = create_app()
        application.dependency_overrides[get_settings] = lambda: settings
        application.dependency_overrides[get_redis] = lambda: AsyncMock()
        application.dependency_overrides[get_db] = fake_db
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/auth/login",
                json={"email": "elise@example.com", "password": "CorrectHorseBattery1!"},
                headers={
                    **_origin_headers(settings),
                    "X-Real-IP": "203.0.113.50",
                },
            )
        application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert resp.status_code == 200
    assert login_mock.await_args.kwargs["ip"] == "203.0.113.50"


@pytest.mark.asyncio
async def test_refresh_records_nginx_x_real_ip_when_proxy_trusted() -> None:
    """Refresh rotation must store the same proxy-resolved client IP as login."""
    settings = Settings(
        environment="local",
        jwt_secret="b" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        trust_x_forwarded_for=True,
        refresh_cookie_name="itr_refresh",
        csrf_cookie_name="itr_csrf",
        csrf_header_name="X-CSRF-Token",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )
    result = _token_result()
    csrf = mint_csrf(settings.jwt_secret)
    mock_session = AsyncMock()
    mock_session.begin = lambda: AsyncMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=None),
    )

    async def fake_db():
        yield mock_session

    get_settings.cache_clear()
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
        patch(
            "app.api.auth.routes.auth_service.refresh", AsyncMock(return_value=result)
        ) as refresh_mock,
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        application = create_app()
        application.dependency_overrides[get_settings] = lambda: settings
        application.dependency_overrides[get_redis] = lambda: AsyncMock()
        application.dependency_overrides[get_db] = fake_db
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set(settings.refresh_cookie_name, "opaque", path="/auth")
            client.cookies.set(settings.csrf_cookie_name, csrf, path="/")
            resp = await client.post(
                "/auth/refresh",
                headers={
                    **_origin_headers(settings),
                    settings.csrf_header_name: csrf,
                    "X-Real-IP": "203.0.113.50",
                },
            )
        application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert resp.status_code == 200
    assert refresh_mock.await_args.kwargs["ip"] == "203.0.113.50"


@pytest.mark.asyncio
async def test_login_rejects_wrong_origin(app, local_settings: Settings) -> None:
    """Cookie-mutating auth routes reject a foreign Origin."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/auth/login",
            json={"email": "elise@example.com", "password": "CorrectHorseBattery1!"},
            headers={"Origin": "https://evil.example"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cors_preflight(app, local_settings: Settings) -> None:
    """CORS preflight allows the configured frontend origin."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.options(
            "/auth/login",
            headers={
                "Origin": local_settings.frontend_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert resp.status_code in {200, 204}
    assert resp.headers.get("access-control-allow-origin") == local_settings.frontend_origin
    assert resp.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.asyncio
async def test_cors_preflight_allows_skills_put_delete(app, local_settings: Settings) -> None:
    """Skills mutations need PUT/DELETE in CORS allow_methods for browser clients."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for method in ("PUT", "DELETE"):
            resp = await client.options(
                "/api/skills",
                headers={
                    "Origin": local_settings.frontend_origin,
                    "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )
            assert resp.status_code in {200, 204}, method
            allow = resp.headers.get("access-control-allow-methods", "")
            assert method in allow.upper(), f"{method} missing from {allow!r}"


@pytest.mark.asyncio
async def test_cors_preflight_allows_reply_memory_patch(app, local_settings: Settings) -> None:
    """Reply-memory exclude uses PATCH; browsers preflight it cross-origin."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.options(
            "/api/reply-memory/00000000-0000-0000-0000-000000000001",
            headers={
                "Origin": local_settings.frontend_origin,
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert resp.status_code in {200, 204}
        allow = resp.headers.get("access-control-allow-methods", "")
        assert "PATCH" in allow.upper(), f"PATCH missing from {allow!r}"


@pytest.mark.asyncio
async def test_change_password_requires_csrf(app, local_settings: Settings) -> None:
    """Change-password without CSRF is forbidden even with a Bearer token."""
    from app.core.dependencies_auth import get_current_user

    user = UserMe(
        id=uuid.uuid4(),
        email="elise@example.com",
        role="user",
        created_at=datetime.now(UTC),
    )

    async def fake_user() -> UserMe:
        return user

    app.dependency_overrides[get_current_user] = fake_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/auth/change-password",
            json={
                "current_password": "CorrectHorseBattery1!",
                "new_password": "CorrectHorseBattery2!",
            },
            headers={
                **_origin_headers(local_settings),
                "Authorization": "Bearer access.jwt.token",
            },
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_change_password_with_csrf(app, local_settings: Settings) -> None:
    """Change-password with matching CSRF clears cookies after success."""
    from app.core.dependencies_auth import get_current_user

    user = UserMe(
        id=uuid.uuid4(),
        email="elise@example.com",
        role="user",
        created_at=datetime.now(UTC),
    )
    csrf = mint_csrf(local_settings.jwt_secret)
    mock_session = AsyncMock()
    mock_session.begin = lambda: AsyncMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=None),
    )

    async def fake_db():
        yield mock_session

    async def fake_user() -> UserMe:
        return user

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = fake_user
    with patch(
        "app.api.auth.routes.auth_service.change_password",
        AsyncMock(return_value=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set(local_settings.csrf_cookie_name, csrf, path="/")
            client.cookies.set(local_settings.refresh_cookie_name, "opaque", path="/auth")
            resp = await client.post(
                "/auth/change-password",
                json={
                    "current_password": "CorrectHorseBattery1!",
                    "new_password": "CorrectHorseBattery2!",
                },
                headers={
                    **_origin_headers(local_settings),
                    "Authorization": "Bearer access.jwt.token",
                    local_settings.csrf_header_name: csrf,
                },
            )
    assert resp.status_code == 204
    # Cookie-clearing Set-Cookie headers should mention both auth cookies.
    set_cookie = resp.headers.get("set-cookie", "")
    if not set_cookie:
        # httpx may expose multi-value headers via raw list
        set_cookie = " ".join(
            v.decode() if isinstance(v, bytes) else str(v)
            for k, v in resp.headers.multi_items()
            if k.lower() == "set-cookie"
        )
    joined = set_cookie.lower()
    assert local_settings.refresh_cookie_name in joined
    assert local_settings.csrf_cookie_name in joined
