"""Unit tests for rate-limit client IP key selection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.core.rate_limit import client_ip_key


def _request(*, headers: dict[str, str], peer: str = "10.0.0.1") -> MagicMock:
    request = MagicMock()
    # Starlette Headers are case-insensitive; mimic .get on a plain dict lowercased.
    lowered = {k.lower(): v for k, v in headers.items()}
    request.headers.get = lambda key, default=None: lowered.get(key.lower(), default)
    request.client = MagicMock()
    request.client.host = peer
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
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        assert chat_rate_limit_key(request) == f"user:{user_id}"


def test_chat_rate_limit_key_falls_back_to_ip_without_bearer() -> None:
    from app.core.rate_limit import chat_rate_limit_key
    settings = Settings(environment="local", trust_x_forwarded_for=False)
    request = _request(headers={}, peer="10.0.0.1")
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        assert chat_rate_limit_key(request) == "10.0.0.1"
