"""Unit tests for rate-limit client IP key selection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings, get_settings
from app.core.rate_limit import client_ip_key


def _request(*, headers: dict[str, str], peer: str = "10.0.0.1") -> MagicMock:
    request = MagicMock()
    # Starlette Headers are case-insensitive; mimic .get on a plain dict lowercased.
    lowered = {k.lower(): v for k, v in headers.items()}
    request.headers.get = lambda key, default=None: lowered.get(key.lower(), default)
    request.client = MagicMock()
    request.client.host = peer
    request.state = SimpleNamespace()
    return request


def test_client_ip_key_uses_peer_when_untrusted() -> None:
    settings = Settings(environment="local", trust_x_forwarded_for=False)
    request = _request(
        headers={
            "X-Real-IP": "203.0.113.10",
            "X-Forwarded-For": "198.51.100.1, 10.0.0.1",
        },
        peer="10.0.0.1",
    )
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        assert client_ip_key(request) == "10.0.0.1"


def test_client_ip_key_prefers_x_real_ip_when_trusted() -> None:
    """X-Real-IP is overwritten by nginx $remote_addr — not client-spoofable."""
    settings = Settings(environment="local", trust_x_forwarded_for=True)
    request = _request(
        headers={
            "X-Real-IP": "203.0.113.50",
            "X-Forwarded-For": "198.51.100.99, 203.0.113.50",
        },
        peer="10.0.0.1",
    )
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        assert client_ip_key(request) == "203.0.113.50"


def test_client_ip_key_ignores_spoofed_leftmost_xff() -> None:
    """Leftmost XFF must not create a new bucket when only XFF is present."""
    settings = Settings(environment="local", trust_x_forwarded_for=True)
    request = _request(
        headers={"X-Forwarded-For": "198.51.100.1, 203.0.113.50"},
        peer="10.0.0.1",
    )
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        # Rightmost hop = address our proxy appended.
        assert client_ip_key(request) == "203.0.113.50"


def test_limiter_storage_uri_injects_ssl_ca_for_rediss() -> None:
    from app.core.rate_limit import limiter_storage_uri

    settings = Settings(
        environment="production",
        redis_url="rediss://:secret@redis:6379/0",
        redis_ssl_ca_certs="/tls/ca.crt",
    )
    uri = limiter_storage_uri(settings)
    assert uri.startswith("rediss://")
    assert "ssl_ca_certs=%2Ftls%2Fca.crt" in uri or "ssl_ca_certs=/tls/ca.crt" in uri
    assert "ssl_cert_reqs=required" in uri


def test_limiter_storage_uri_local_uses_memory() -> None:
    from app.core.rate_limit import limiter_storage_uri

    settings = Settings(environment="local", redis_url="redis://localhost:6379/0")
    assert limiter_storage_uri(settings) == "memory://"


def test_chat_limit_value_uses_settings() -> None:
    from app.core.rate_limit import chat_limit_value

    settings = Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        chat_rate_limit_per_minute=12,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        _env_file=None,
    )
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        assert chat_limit_value(MagicMock()) == "12/minute"


def test_chat_rate_limit_key_uses_access_token_sub() -> None:
    import uuid

    from app.core.rate_limit import chat_rate_limit_key
    from app.core.security.tokens import create_access_token

    settings = Settings(environment="local", jwt_secret="c" * 64)
    user_id = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    token, _expires = create_access_token(user_id, settings, token_version=0)
    request = _request(headers={"Authorization": f"Bearer {token}"}, peer="10.0.0.1")
    with (
        patch("app.core.rate_limit.get_settings", return_value=settings),
        patch("app.core.rate_limit._load_user_auth", lambda uid: (True, 0)),
    ):
        assert chat_rate_limit_key(request) == f"user:{user_id}"


def test_chat_rate_limit_key_falls_back_to_ip_without_bearer() -> None:
    from app.core.rate_limit import chat_rate_limit_key
    settings = Settings(environment="local", trust_x_forwarded_for=False)
    request = _request(headers={}, peer="10.0.0.1")
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        assert chat_rate_limit_key(request) == "10.0.0.1"


def test_chat_key_reuses_request_state_when_present() -> None:
    """H4: CurrentUser already validated this principal — do not decode again."""
    from app.core.rate_limit import chat_rate_limit_key

    user_id = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    request = _request(
        headers={"Authorization": "Bearer definitely-not-a-jwt"},
        peer="10.0.0.1",
    )
    request.state.user_id = user_id
    with patch(
        "app.core.rate_limit.decode_access_token",
        side_effect=AssertionError("must not decode when request.state.user_id is set"),
    ):
        assert chat_rate_limit_key(request) == f"user:{user_id}"


@pytest.mark.db
@pytest.mark.asyncio
async def test_chat_key_ignores_revoked_token(db_session) -> None:
    """H4: a stolen JWT whose token_version no longer matches the user row
    must not burn the victim's per-user chat quota. Independent oracle: the
    key is the client IP, never ``user:{sub}``.
    """
    from app.core.rate_limit import chat_rate_limit_key
    from app.core.security.tokens import create_access_token
    from app.models.db.user import User

    user_id = uuid.uuid4()
    settings = Settings(environment="local", jwt_secret="c" * 64, trust_x_forwarded_for=False)
    user = User(
        id=user_id,
        email=f"revoked-{user_id.hex[:8]}@example.com",
        password_hash="hashed",
        role="user",
        is_active=True,
        token_version=2,
    )
    db_session.add(user)
    await db_session.commit()

    token, _expires = create_access_token(user_id, settings, token_version=1)
    request = _request(headers={"Authorization": f"Bearer {token}"}, peer="10.0.0.1")

    def load_from_fixture(uid: uuid.UUID) -> tuple[bool, int] | None:
        # Independent oracle: the row we just committed has token_version=2.
        if uid != user_id:
            return None
        return (True, 2)

    with (
        patch("app.core.rate_limit.get_settings", return_value=settings),
        patch("app.core.rate_limit._load_user_auth", load_from_fixture),
    ):
        key = chat_rate_limit_key(request)
    assert key == "10.0.0.1"
    assert key != f"user:{user_id}"


@pytest.mark.asyncio
async def test_chat_ask_rate_limited_per_user_not_per_ip() -> None:
    """Same user from two IPs shares one chat bucket; the 2nd ask is 429."""
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient

    from app.core.dependencies import get_anthropic_client, get_db, get_openai_client
    from app.core.dependencies_auth import get_current_user
    from app.core.rate_limit import limiter
    from app.core.security.tokens import create_access_token
    from app.main import create_app
    from app.models.schemas.auth import UserMe
    from app.models.schemas.chat import ChatAskResponse

    user_id = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    settings = Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes="sales@example.com",
        anthropic_api_key="sk-ant-test",
        chat_rate_limit_per_minute=1,
        trust_x_forwarded_for=True,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        _env_file=None,
    )
    storage = getattr(getattr(limiter, "_limiter", limiter), "_storage", None)
    reset = getattr(storage, "reset", None)
    if callable(reset):
        reset()

    get_settings.cache_clear()
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
        patch("app.core.rate_limit.get_settings", return_value=settings),
        patch("app.core.rate_limit._load_user_auth", lambda uid: (True, 0)),
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        application = create_app()
        application.dependency_overrides[get_settings] = lambda: settings

        async def fake_user() -> UserMe:
            return UserMe(
                id=user_id,
                email="elise@example.com",
                role="user",
                created_at=datetime.now(UTC),
            )

        application.dependency_overrides[get_current_user] = fake_user
        application.dependency_overrides[get_db] = lambda: AsyncMock()
        application.dependency_overrides[get_openai_client] = lambda: None
        application.dependency_overrides[get_anthropic_client] = lambda: None

        token, _expires = create_access_token(user_id, settings, token_version=0)
        ok_response = ChatAskResponse(
            answer="Focus on billing.",
            citations=[],
            retrieval_count=0,
            mailbox="sales@example.com",
            refused_write=False,
        )
        with patch("app.api.web.chat.chat_service.ask", AsyncMock(return_value=ok_response)):
            transport = ASGITransport(app=application)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                first = await client.post(
                    "/api/chat/ask",
                    json={"message": "billing disputes waiting on review"},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Real-IP": "203.0.113.10",
                    },
                )
                second = await client.post(
                    "/api/chat/ask",
                    json={"message": "billing disputes waiting on review"},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Real-IP": "198.51.100.20",
                    },
                )
        application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert first.status_code == 200, first.text
    assert second.status_code == 429, second.text
