"""Shared SlowAPI limiter instance.

Docs:
- https://slowapi.readthedocs.io/en/latest/
- https://limits.readthedocs.io/en/stable/storage.html#redis-ssl-storage
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import jwt
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import Settings, get_settings
from app.core.security.tokens import decode_access_token

# Short TTL so a password-change / deactivate is picked up without a restart.
# SlowAPI's key_func is synchronous (slowapi.extension.Limiter._check_request_limit
# does not await), so CurrentUser cannot populate request.state in time for the
# first limiter check. We look the user up ourselves and cache the row.
_AUTH_CACHE_TTL_SEC = 5.0
_auth_cache: dict[uuid.UUID, tuple[bool, int, float]] = {}
_auth_cache_lock = threading.Lock()


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


def context_rebuild_bucket(thread_id: str, ip: str) -> str:
    return "ctx-rebuild:" + thread_id + ":" + ip


def context_rebuild_rate_limit_key(request: Request) -> str:
    """Per-thread rebuild budget; IP fallback keeps anonymous hammering cheap."""
    thread_id = str(request.path_params.get("thread_id") or "")
    return context_rebuild_bucket(thread_id, client_ip_key(request))


def invalidate_user_auth_cache(user_id: uuid.UUID) -> None:
    """Drop a cached (is_active, token_version) after password / revoke."""
    with _auth_cache_lock:
        _auth_cache.pop(user_id, None)


def remember_user_auth(user_id: uuid.UUID, *, is_active: bool, token_version: int) -> None:
    """Filled by CurrentUser after a successful is_active + token_version check."""
    with _auth_cache_lock:
        _auth_cache[user_id] = (is_active, token_version, time.monotonic())


def _cached_user_auth(user_id: uuid.UUID) -> tuple[bool, int] | None:
    with _auth_cache_lock:
        row = _auth_cache.get(user_id)
    if row is None:
        return None
    is_active, token_version, stored_at = row
    if time.monotonic() - stored_at > _AUTH_CACHE_TTL_SEC:
        invalidate_user_auth_cache(user_id)
        return None
    return is_active, token_version


def _load_user_auth(user_id: uuid.UUID) -> tuple[bool, int] | None:
    """Return (is_active, token_version) for ``user_id``, or None if unknown.

    SlowAPI key functions cannot await, so this opens a short-lived session on
    a private event loop (new thread when one is already running). Tests patch
    this symbol to inject a fixture row without touching the engine.
    """
    cached = _cached_user_auth(user_id)
    if cached is not None:
        return cached

    async def _fetch() -> tuple[bool, int] | None:
        from app.db.session import get_session_factory
        from app.repositories import user_repo

        factory = get_session_factory()
        async with factory() as session:
            user = await user_repo.get_by_id(session, user_id)
        if user is None:
            return None
        remember_user_auth(user.id, is_active=user.is_active, token_version=user.token_version)
        return bool(user.is_active), int(user.token_version)

    def _run() -> tuple[bool, int] | None:
        try:
            return asyncio.run(_fetch())
        except Exception:
            return None

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run()

    holder: list[tuple[bool, int] | None] = []

    def _in_thread() -> None:
        holder.append(_run())

    thread = threading.Thread(target=_in_thread, daemon=True)
    thread.start()
    thread.join(timeout=2)
    return holder[0] if holder else None


def chat_rate_limit_key(request: Request) -> str:
    """Per-user chat budget after auth validation; otherwise client IP.

    Prefer ``request.state.user_id`` (set by CurrentUser after is_active and
    token_version checks) so a JWT is decoded at most once per request. When
    SlowAPI runs first — it does, on every chat route — decode the bearer,
    load the user, and trust ``sub`` only if the stored token_version still
    matches. A revoked or inactive token falls back to the IP bucket so it
    cannot burn the victim's per-user quota.
    """
    cached_id = getattr(request.state, "user_id", None)
    if cached_id is not None and str(cached_id).strip():
        return f"user:{cached_id}"

    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        try:
            claims = decode_access_token(token, get_settings())
            user_id = uuid.UUID(str(claims.sub))
            loaded = _load_user_auth(user_id)
            if loaded is not None and loaded[0] and loaded[1] == claims.token_version:
                return f"user:{claims.sub}"
        except (jwt.PyJWTError, ValueError):
            pass
    return client_ip_key(request)


def chat_limit_value(request: Request | None = None) -> str:
    _ = request
    return f"{get_settings().chat_rate_limit_per_minute}/minute"


def api_default_limit_value(request: Request | None = None) -> str:
    _ = request
    return get_settings().api_default_rate_limit


def auth_login_limit_value(request: Request | None = None) -> str:
    _ = request
    return get_settings().auth_login_rate_limit


def auth_refresh_limit_value(request: Request | None = None) -> str:
    _ = request
    return get_settings().auth_refresh_rate_limit


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
