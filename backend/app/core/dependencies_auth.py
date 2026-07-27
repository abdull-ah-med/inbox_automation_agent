"""Auth FastAPI dependencies: current user + CSRF + Origin validation."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.security.csrf import validate_csrf
from app.core.security.tokens import decode_access_token
from app.models.schemas.auth import UserMe
from app.repositories import user_repo

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    session: DbSession,
    settings: AppSettings,
    authorization: Annotated[str | None, Header()] = None,
) -> UserMe:
    """Require a valid Bearer access token and an active user."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    try:
        claims = decode_access_token(token, settings)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    user = await user_repo.get_by_id(session, claims.sub)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if claims.token_version != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return UserMe.model_validate(user)


def require_csrf(request: Request, settings: AppSettings) -> None:
    """Double-submit CSRF for cookie-authenticated mutating auth routes."""
    cookie_name = settings.csrf_cookie_name
    header_name = settings.csrf_header_name
    cookie_value = request.cookies.get(cookie_name)
    header_value = request.headers.get(header_name)
    if not validate_csrf(settings.jwt_secret, cookie_value, header_value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )


def require_frontend_origin(request: Request, settings: AppSettings) -> None:
    """Reject cookie-mutating auth requests that do not come from FRONTEND_ORIGIN.

    Browsers send Origin on cross-origin credentialed fetches. Referer is a
    fallback for same-origin edge cases. Missing both is allowed only in local.
    """
    allowed = settings.frontend_origin.strip().rstrip("/")
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if origin:
        if origin != allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid origin",
            )
        return

    referer = (request.headers.get("referer") or "").strip()
    if referer:
        parsed = urlparse(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if referer_origin != allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid origin",
            )
        return

    if settings.environment == "local":
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Missing origin",
    )


CurrentUser = Annotated[UserMe, Depends(get_current_user)]
RequireCsrf = Annotated[None, Depends(require_csrf)]
