"""Auth service unit tests with mocked repos."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import (
    InvalidCredentialsError,
    InvalidTokenError,
    ReusedRefreshTokenError,
)
from app.core.security.password import hash_password
from app.core.security.tokens import generate_refresh_token
from app.repositories.user_repo import UserRead
from app.services import auth_service


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="z" * 64,
        access_token_ttl_seconds=900,
        refresh_token_ttl_seconds=604_800,
        refresh_rotation_grace_seconds=10,
    )


def _user(**overrides: object) -> UserRead:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "email": "elise@example.com",
        "password_hash": hash_password("CorrectHorseBattery1!"),
        "password_updated_at": now,
        "token_version": 0,
        "role": "user",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return UserRead.model_validate(base)


@pytest.mark.asyncio
async def test_login_happy_path(settings: Settings) -> None:
    """Valid credentials issue access + refresh tokens."""
    user = _user()
    session = AsyncMock()
    with (
        patch("app.services.auth_service.user_repo.get_by_email", AsyncMock(return_value=user)),
        patch(
            "app.services.auth_service.refresh_token_repo.insert",
            AsyncMock(return_value=None),
        ),
    ):
        result = await auth_service.login(
            session,
            settings,
            email=user.email,
            password="CorrectHorseBattery1!",
            user_agent="test",
            ip="127.0.0.1",
        )
    assert result.response.user.email == user.email
    assert result.response.access_token
    assert result.refresh_plaintext


@pytest.mark.asyncio
async def test_login_wrong_password(settings: Settings) -> None:
    """Wrong password raises a generic InvalidCredentialsError."""
    user = _user()
    session = AsyncMock()
    with patch("app.services.auth_service.user_repo.get_by_email", AsyncMock(return_value=user)):
        with pytest.raises(InvalidCredentialsError):
            await auth_service.login(
                session,
                settings,
                email=user.email,
                password="WrongPassword1!",
                user_agent=None,
                ip=None,
            )


@pytest.mark.asyncio
async def test_login_missing_user(settings: Settings) -> None:
    """Unknown email raises the same generic error."""
    session = AsyncMock()
    with patch("app.services.auth_service.user_repo.get_by_email", AsyncMock(return_value=None)):
        with pytest.raises(InvalidCredentialsError):
            await auth_service.login(
                session,
                settings,
                email="nobody@example.com",
                password="CorrectHorseBattery1!",
                user_agent=None,
                ip=None,
            )


@pytest.mark.asyncio
async def test_refresh_reuse_revokes_family(settings: Settings) -> None:
    """Presenting an already-revoked refresh token revokes the family."""
    from app.repositories.refresh_token_repo import RefreshTokenRead

    plaintext, digest = generate_refresh_token()
    now = datetime.now(UTC)
    stored = RefreshTokenRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        token_hash=digest,
        family_id=uuid.uuid4(),
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=6),
        revoked_at=now - timedelta(minutes=1),
        replaced_by_id=uuid.uuid4(),
        user_agent=None,
        ip=None,
    )
    session = AsyncMock()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    revoke_family = AsyncMock(return_value=2)
    with (
        patch(
            "app.services.auth_service.refresh_token_repo.get_by_hash_for_update",
            AsyncMock(return_value=stored),
        ),
        patch(
            "app.services.auth_service.refresh_token_repo.revoke_family",
            revoke_family,
        ),
    ):
        with pytest.raises(ReusedRefreshTokenError):
            await auth_service.refresh(
                session,
                settings,
                redis,
                refresh_plaintext=plaintext,
                user_agent=None,
                ip=None,
            )
    revoke_family.assert_awaited_once_with(session, stored.family_id)


@pytest.mark.asyncio
async def test_refresh_expired(settings: Settings) -> None:
    """Expired refresh token is revoked and rejected."""
    from app.repositories.refresh_token_repo import RefreshTokenRead

    plaintext, digest = generate_refresh_token()
    now = datetime.now(UTC)
    stored = RefreshTokenRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        token_hash=digest,
        family_id=uuid.uuid4(),
        issued_at=now - timedelta(days=8),
        expires_at=now - timedelta(hours=1),
        revoked_at=None,
        replaced_by_id=None,
        user_agent=None,
        ip=None,
    )
    session = AsyncMock()
    redis = AsyncMock()
    with (
        patch(
            "app.services.auth_service.refresh_token_repo.get_by_hash_for_update",
            AsyncMock(return_value=stored),
        ),
        patch("app.services.auth_service.refresh_token_repo.revoke", AsyncMock()),
    ):
        with pytest.raises(InvalidTokenError):
            await auth_service.refresh(
                session,
                settings,
                redis,
                refresh_plaintext=plaintext,
                user_agent=None,
                ip=None,
            )


@pytest.mark.asyncio
async def test_change_password_revokes_all(settings: Settings) -> None:
    """Password change revokes every refresh token for the user."""
    user = _user()
    session = AsyncMock()
    revoke_all = AsyncMock(return_value=3)
    with (
        patch("app.services.auth_service.user_repo.get_by_id", AsyncMock(return_value=user)),
        patch("app.services.auth_service.user_repo.update_password", AsyncMock()),
        patch(
            "app.services.auth_service.refresh_token_repo.revoke_all_for_user",
            revoke_all,
        ),
    ):
        await auth_service.change_password(
            session,
            user_id=user.id,
            current_password="CorrectHorseBattery1!",
            new_password="BrandNewPassword2!",
        )
    revoke_all.assert_awaited_once_with(session, user.id)
