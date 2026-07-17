"""Guards for local-only debug/simulate routers."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, status

from app.core.config import Settings


def require_local_dev_access(
    settings: Settings,
    x_dev_api_key: str | None = None,
) -> None:
    """Enforce local + ENABLE_DEV_ROUTES + matching X-Dev-Api-Key.

    Router mounting is also gated in ``create_app``; this is defense in depth.
    """
    if settings.environment != "local" or not settings.enable_dev_routes:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    expected = settings.dev_api_key.strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DEV_API_KEY is not configured",
        )
    provided = (x_dev_api_key or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Dev-Api-Key",
        )
