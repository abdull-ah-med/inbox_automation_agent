"""Shared SlowAPI limiter instance.

Docs:
- https://slowapi.readthedocs.io/en/latest/
- https://limits.readthedocs.io/en/stable/storage.html#redis-ssl-storage
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import jwt
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import Settings, get_settings


def resolve_client_ip(request: Request, settings: Settings) -> str:
    """Rate-limit key IP from peer or trusted proxy headers.

    Enable ``TRUST_X_FORWARDED_FOR`` only behind a reverse proxy you control.

    Prefer ``X-Real-IP``: nginx ``proxy_set_header X-Real-IP $remote_addr``
    overwrites the header with the TCP peer, so clients cannot spoof it.
    Do **not** use the leftmost ``X-Forwarded-For`` hop — with
    ``$proxy_add_x_forwarded_for`` that prefix is client-controlled.

    Fallback when X-Real-IP is absent: the **rightmost** XFF hop (the address
    our proxy appended). Still prefer configuring nginx to set
    ``X-Forwarded-For $remote_addr`` (replace, not append).

    Refs:
    - https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header
    - https://www.rfc-editor.org/rfc/rfc7239 (Forwarded / XFF semantics)
    """
    if settings.trust_x_forwarded_for:
        real_ip = (request.headers.get("x-real-ip") or "").strip()
        if real_ip:
            return real_ip
        xff = (request.headers.get("x-forwarded-for") or "").strip()
        if xff:
            # Rightmost hop is what a correctly appending proxy added.
            client = xff.split(",")[-1].strip()
            if client:
                return client
    return get_remote_address(request)


def client_ip_key(request: Request) -> str:
    """SlowAPI key func: resolve IP using process settings."""
    return resolve_client_ip(request, get_settings())


def chat_rate_limit_key(request: Request) -> str:
    """Per-user chat budget: Bearer ``sub`` when present, otherwise client IP."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        try:
            from app.core.security.tokens import decode_access_token

            claims = decode_access_token(token, get_settings())
            return f"user:{claims.sub}"
        except jwt.PyJWTError:
            pass
    return client_ip_key(request)


def chat_limit_value(request: Request | None = None) -> str:
    _ = request
    return f"{get_settings().chat_rate_limit_per_minute}/minute"


def limiter_storage_uri(settings: Settings) -> str:
    """Build SlowAPI/limits storage URI, injecting TLS CA when configured.

    limits Redis+SSL docs allow ``ssl_ca_certs`` / ``ssl_cert_reqs`` as URL
    query params (passed through to redis-py):
    https://limits.readthedocs.io/en/stable/storage.html#redis-ssl-storage
    """
    if settings.environment == "local":
        return "memory://"

    url = settings.redis_url
    ca_certs = settings.redis_ssl_ca_certs.strip()
    if not (settings.redis_uses_tls and ca_certs):
        return url

    parsed = urlparse(url)
    qs = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=False).items()}
    qs["ssl_cert_reqs"] = "required"
    qs["ssl_ca_certs"] = ca_certs
    return urlunparse(parsed._replace(query=urlencode(qs)))


def build_limiter() -> Limiter:
    settings = get_settings()
    return Limiter(
        key_func=client_ip_key,
        default_limits=[settings.api_default_rate_limit],
        storage_uri=limiter_storage_uri(settings),
        headers_enabled=True,
        # Memory fallback is only safe for single-process local dev.
        in_memory_fallback_enabled=settings.environment == "local",
    )


limiter = build_limiter()
