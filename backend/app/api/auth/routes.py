"""Auth HTTP routes: login, refresh, logout, me, change-password."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_redis
from app.core.dependencies_auth import (
    CurrentUser,
    require_csrf,
    require_frontend_origin,
)
from app.core.rate_limit import limiter
from app.core.security.csrf import mint_csrf
from app.models.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
    UserMe,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
RedisClient = Annotated[Redis, Depends(get_redis)]


def _cookie_domain(settings: Settings) -> str | None:
    domain = settings.cookie_domain.strip()
    if domain and settings.environment != "local":
        return domain
    return None


def _set_refresh_cookie(
    response: Response,
    settings: Settings,
    refresh_plaintext: str,
) -> None:
    """HttpOnly refresh cookie scoped to /auth (refresh + logout only)."""
    max_age = settings.refresh_token_ttl_seconds
    cookie_kwargs: dict[str, object] = {
        "key": settings.refresh_cookie_name,
        "value": refresh_plaintext,
        "max_age": max_age,
        "httponly": True,
        "secure": settings.cookie_secure if settings.environment != "local" else False,
        "samesite": "strict",
        "path": "/auth",
    }
    domain = _cookie_domain(settings)
    if domain is not None:
        cookie_kwargs["domain"] = domain
    response.set_cookie(**cookie_kwargs)  # type: ignore[arg-type]


def _set_csrf_cookie(response: Response, settings: Settings) -> str:
    """Non-HttpOnly CSRF cookie; Domain must match refresh so the SPA can read it."""
    token = mint_csrf(settings.jwt_secret)
    cookie_kwargs: dict[str, object] = {
        "key": settings.csrf_cookie_name,
        "value": token,
        "max_age": settings.refresh_token_ttl_seconds,
        "httponly": False,
        "secure": settings.cookie_secure if settings.environment != "local" else False,
        "samesite": "strict",
        "path": "/",
    }
    domain = _cookie_domain(settings)
    if domain is not None:
        cookie_kwargs["domain"] = domain
    response.set_cookie(**cookie_kwargs)  # type: ignore[arg-type]
    return token


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Clear auth cookies using the same Domain/Path/Secure attrs used at set time."""
    secure = settings.cookie_secure if settings.environment != "local" else False
    domain = _cookie_domain(settings)
    refresh_kwargs: dict[str, object] = {
        "key": settings.refresh_cookie_name,
        "path": "/auth",
        "secure": secure,
        "httponly": True,
        "samesite": "strict",
    }
    csrf_kwargs: dict[str, object] = {
        "key": settings.csrf_cookie_name,
        "path": "/",
        "secure": secure,
        "httponly": False,
        "samesite": "strict",
    }
    if domain is not None:
        refresh_kwargs["domain"] = domain
        csrf_kwargs["domain"] = domain
    response.delete_cookie(**refresh_kwargs)  # type: ignore[arg-type]
    response.delete_cookie(**csrf_kwargs)  # type: ignore[arg-type]


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_frontend_origin)],
)
@limiter.limit(get_settings().auth_login_rate_limit)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> TokenResponse:
    async with session.begin():
        result = await auth_service.login(
            session,
            settings,
            email=body.email,
            password=body.password,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
    _set_refresh_cookie(response, settings, result.refresh_plaintext)
    _set_csrf_cookie(response, settings)
    return result.response


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_frontend_origin), Depends(require_csrf)],
)
@limiter.limit(get_settings().auth_refresh_rate_limit)
async def refresh(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    redis: RedisClient,
) -> TokenResponse:
    refresh_plaintext = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_plaintext:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing refresh token",
        )

    async with session.begin():
        result = await auth_service.refresh(
            session,
            settings,
            redis,
            refresh_plaintext=refresh_plaintext,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
    _set_refresh_cookie(response, settings, result.refresh_plaintext)
    _set_csrf_cookie(response, settings)
    return result.response


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(require_frontend_origin)],
)
@limiter.limit(get_settings().auth_refresh_rate_limit)
async def logout(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> None:
    # Require CSRF when the cookie is present; always clear cookies so the SPA
    # can recover from a half-broken session (missing CSRF).
    if request.cookies.get(settings.csrf_cookie_name):
        require_csrf(request, settings)
    refresh_plaintext = request.cookies.get(settings.refresh_cookie_name)
    if refresh_plaintext:
        async with session.begin():
            await auth_service.logout(session, refresh_plaintext=refresh_plaintext)
    _clear_auth_cookies(response, settings)


@router.get(
    "/me",
    response_model=UserMe,
    status_code=status.HTTP_200_OK,
)
@limiter.limit(get_settings().api_default_rate_limit)
async def me(request: Request, response: Response, user: CurrentUser) -> UserMe:
    _ = request, response
    return user


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(require_frontend_origin)],
)
@limiter.limit(get_settings().auth_login_rate_limit)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
) -> None:
    async with session.begin():
        await auth_service.change_password(
            session,
            user_id=user.id,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    _clear_auth_cookies(response, settings)
