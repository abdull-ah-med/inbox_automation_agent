"""Shared SlowAPI limiter instance.

Docs: https://slowapi.readthedocs.io/en/latest/
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings


def client_ip_key(request: Request) -> str:
    """Rate-limit key: peer IP, or leftmost X-Forwarded-For when trusted.

    Enable ``TRUST_X_FORWARDED_FOR`` only behind a reverse proxy that
    overwrites/appends XFF; otherwise clients can spoof the key.
    """
    settings = get_settings()
    if settings.trust_x_forwarded_for:
        xff = (request.headers.get("x-forwarded-for") or "").strip()
        if xff:
            # Leftmost hop is the original client when the proxy appends.
            client = xff.split(",")[0].strip()
            if client:
                return client
    return get_remote_address(request)


def build_limiter() -> Limiter:
    settings = get_settings()
    storage = settings.redis_url if settings.environment != "local" else "memory://"
    return Limiter(
        key_func=client_ip_key,
        default_limits=[settings.api_default_rate_limit],
        storage_uri=storage,
        headers_enabled=True,
        # Memory fallback is only safe for single-process local dev.
        in_memory_fallback_enabled=settings.environment == "local",
    )


limiter = build_limiter()
