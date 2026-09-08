"""Auth service — login, refresh rotation, logout, password change.

Repos own no SQL; they call repository functions and raise domain exceptions.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import (
    InvalidCredentialsError,
    InvalidTokenError,
    ReusedRefreshTokenError,
)
from app.core.redis_keys import refresh_grace_key
from app.core.security.cache_crypto import (
    CacheEncryptionError,
    decrypt_cache_blob,
    encrypt_cache_blob,
)
from app.core.security.password import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.core.security.tokens import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from app.models.schemas.auth import TokenResponse, UserMe
from app.repositories import refresh_token_repo, user_repo

logger = structlog.get_logger(__name__)

# Precomputed dummy hash so missing-user login still runs verify (timing).
_DUMMY_PASSWORD_HASH = hash_password("timing-pad-unused-not-a-real-password")


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Access token payload plus the opaque refresh plaintext for the cookie.

    ``rotated_from_hash`` is set only after a successful refresh rotation.
    Callers must store Redis grace *after* the DB transaction commits.
    """

    response: TokenResponse
    refresh_plaintext: str
    rotated_from_hash: str | None = None


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    role: str = "user",
) -> UserMe:
    """Invite-only user creation (CLI / admin)."""
    # Same policy as ChangePasswordRequest / seed_user CLI (OWASP length + classes).
    validate_password_strength(password)
    existing = await user_repo.get_by_email(session, email)
    if existing is not None:
        raise InvalidCredentialsError("User already exists")
    user = await user_repo.create(
        session,
        email=email,
        password_hash=hash_password(password),
        role=role,
    )
    return UserMe.model_validate(user)


async def login(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    user_agent: str | None,
    ip: str | None,
) -> AuthResult:
    user = await user_repo.get_by_email(session, email)
    # Always verify against a real Argon2 hash when user is missing (timing).
    if user is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise InvalidCredentialsError("Invalid email or password")
    if not user.is_active:
        raise InvalidCredentialsError("Invalid email or password")
    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("Invalid email or password")

    if needs_rehash(user.password_hash):
        # Hash-only upgrade: do not bump password_updated_at / token_version
        # (those invalidate refresh sessions as if the password changed).
        await user_repo.update_password_hash_only(session, user.id, hash_password(password))

    return await _issue_tokens(
        session,
        settings,
        user_id=user.id,
        email=user.email,
        role=user.role,
        created_at=user.created_at,
        token_version=user.token_version,
        user_agent=user_agent,
        ip=ip,
        family_id=uuid.uuid4(),
    )


async def refresh(
    session: AsyncSession,
    settings: Settings,
    redis: Redis,
    *,
    refresh_plaintext: str,
    user_agent: str | None,
    ip: str | None,
) -> AuthResult:
    token_hash = hash_refresh_token(refresh_plaintext)
    stored = await refresh_token_repo.get_by_hash_for_update(session, token_hash)
    if stored is None:
        raise InvalidTokenError("Invalid refresh token")

    if stored.revoked_at is not None:
        # Concurrent refresh / network retry: return the same pair within grace.
        grace = await _load_refresh_grace(redis, settings, token_hash)
        if grace is not None:
            return grace

        # Reuse detection — revoke the whole family.
        await refresh_token_repo.revoke_family(session, stored.family_id)
        logger.warning(
            "refresh_token_reuse_detected",
            family_id=str(stored.family_id),
            user_id=str(stored.user_id),
        )
        raise ReusedRefreshTokenError("Refresh token reuse detected")

    now = datetime.now(UTC)
    if stored.expires_at <= now:
        await refresh_token_repo.revoke(session, stored.id)
        raise InvalidTokenError("Refresh token expired")

    user = await user_repo.get_by_id(session, stored.user_id)
    if user is None or not user.is_active:
        await refresh_token_repo.revoke_family(session, stored.family_id)
        raise InvalidTokenError("Invalid refresh token")

    # Password change invalidates all refresh tokens issued before the change.
    if user.password_updated_at > stored.issued_at:
        await refresh_token_repo.revoke_all_for_user(session, user.id)
        raise InvalidTokenError("Refresh token revoked")

    new_plaintext, new_hash = generate_refresh_token()
    expires_at = now + timedelta(seconds=settings.refresh_token_ttl_seconds)
    new_row = await refresh_token_repo.insert(
        session,
        user_id=user.id,
        token_hash=new_hash,
        family_id=stored.family_id,
        expires_at=expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    claimed = await refresh_token_repo.revoke(
        session,
        stored.id,
        replaced_by_id=new_row.id,
    )
    if not claimed:
        # Lost the race after FOR UPDATE (should be rare); treat as reuse/grace.
        grace = await _load_refresh_grace(redis, settings, token_hash)
        if grace is not None:
            return grace
        await refresh_token_repo.revoke_family(session, stored.family_id)
        raise ReusedRefreshTokenError("Refresh token reuse detected")

    access_token, expires_in = create_access_token(
        user.id,
        settings,
        token_version=user.token_version,
    )
    response = TokenResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=UserMe.model_validate(user),
    )
    # Grace is stored by the route after session.begin() commits successfully.
    return AuthResult(
        response=response,
        refresh_plaintext=new_plaintext,
        rotated_from_hash=token_hash,
    )


async def store_refresh_grace(
    redis: Redis,
    settings: Settings,
    old_token_hash: str,
    result: AuthResult,
) -> None:
    """Public wrapper: persist rotation grace after the DB commit succeeds."""
    await _store_refresh_grace(redis, settings, old_token_hash, result)


async def logout(session: AsyncSession, *, refresh_plaintext: str) -> None:
    token_hash = hash_refresh_token(refresh_plaintext)
    stored = await refresh_token_repo.get_by_hash(session, token_hash)
    if stored is None:
        return
    if stored.revoked_at is None:
        await refresh_token_repo.revoke(session, stored.id)


async def change_password(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    current_password: str,
    new_password: str,
) -> None:
    user = await user_repo.get_by_id(session, user_id)
    if user is None or not user.is_active:
        raise InvalidCredentialsError("Invalid credentials")
    if not verify_password(current_password, user.password_hash):
        raise InvalidCredentialsError("Invalid credentials")
    await user_repo.update_password(session, user_id, hash_password(new_password))
    await refresh_token_repo.revoke_all_for_user(session, user_id)
    from app.core.rate_limit import invalidate_user_auth_cache

    invalidate_user_auth_cache(user_id)
    logger.info("password_changed", user_id=str(user_id))


async def _issue_tokens(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    email: str,
    role: str,
    created_at: datetime,
    token_version: int,
    user_agent: str | None,
    ip: str | None,
    family_id: uuid.UUID,
) -> AuthResult:
    plaintext, token_hash = generate_refresh_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds)
    await refresh_token_repo.insert(
        session,
        user_id=user_id,
        token_hash=token_hash,
        family_id=family_id,
        expires_at=expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    access_token, expires_in = create_access_token(
        user_id,
        settings,
        token_version=token_version,
    )
    response = TokenResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=UserMe(id=user_id, email=email, role=role, created_at=created_at),
    )
    return AuthResult(response=response, refresh_plaintext=plaintext)


def _grace_encryption_key(settings: Settings) -> str | None:
    """Reuse MSAL Fernet key when set (required outside local)."""
    key = settings.msal_cache_encryption_key.strip()
    return key or None


def _encode_refresh_grace(settings: Settings, payload_json: str) -> str:
    """Encrypt grace JSON when a Fernet key is configured (same as MSAL cache).

    Fernet: https://cryptography.io/en/latest/fernet/
    """
    key = _grace_encryption_key(settings)
    if key is None:
        return payload_json
    try:
        return encrypt_cache_blob(payload_json, key)
    except CacheEncryptionError:
        logger.exception("refresh_grace_encryption_key_invalid")
        raise


def _decode_refresh_grace(settings: Settings, raw: str) -> str | None:
    """Decrypt grace payload. No plaintext fallback when encryption is configured.

    Pre-encryption grace entries expire within ``refresh_rotation_grace_seconds``
    (≤60s). Accepting InvalidToken as plaintext would let a Redis writer forge
    grace payloads during rotation.
    """
    key = _grace_encryption_key(settings)
    if key is None:
        return raw
    try:
        return decrypt_cache_blob(raw, key)
    except CacheEncryptionError:
        logger.exception("refresh_grace_encryption_key_invalid")
        return None


async def _store_refresh_grace(
    redis: Redis,
    settings: Settings,
    old_token_hash: str,
    result: AuthResult,
) -> None:
    ttl = settings.refresh_rotation_grace_seconds
    if ttl <= 0:
        return
    payload = {
        "refresh_plaintext": result.refresh_plaintext,
        "access_token": result.response.access_token,
        "expires_in": result.response.expires_in,
        "user": result.response.user.model_dump(mode="json"),
    }
    await redis.set(
        refresh_grace_key(old_token_hash),
        _encode_refresh_grace(settings, json.dumps(payload)),
        ex=ttl,
    )


async def _load_refresh_grace(
    redis: Redis,
    settings: Settings,
    old_token_hash: str,
) -> AuthResult | None:
    raw = await redis.get(refresh_grace_key(old_token_hash))
    if not raw:
        return None
    try:
        payload = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
        decoded = _decode_refresh_grace(settings, payload)
        if decoded is None:
            return None
        data = json.loads(decoded)
        user = UserMe.model_validate(data["user"])
        response = TokenResponse(
            access_token=data["access_token"],
            expires_in=int(data["expires_in"]),
            user=user,
        )
        return AuthResult(
            response=response,
            refresh_plaintext=data["refresh_plaintext"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        logger.warning("refresh_grace_cache_corrupt")
        return None
