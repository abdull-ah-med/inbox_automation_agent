"""JWT access tokens and opaque refresh tokens.

PyJWT docs: https://pyjwt.readthedocs.io/en/stable/
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class AccessClaims:
    """Validated claims from a decoded access token."""

    sub: uuid.UUID
    exp: datetime
    iat: datetime
    iss: str
    aud: str
    jti: str
    token_version: int


def create_access_token(
    user_id: uuid.UUID,
    settings: Settings,
    *,
    token_version: int = 0,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, int]:
    """Issue a short-lived HS256 JWT. Returns ``(token, expires_in_seconds)``.

    Required claims are applied after ``extra_claims`` so callers cannot
    overwrite ``sub`` / ``exp`` / ``iss`` / ``aud`` / ``tv``.
    """
    now = datetime.now(UTC)
    expires_in = settings.access_token_ttl_seconds
    payload: dict[str, Any] = {}
    if extra_claims:
        payload.update(extra_claims)
    payload.update(
        {
            "sub": str(user_id),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(seconds=expires_in),
            "jti": str(uuid.uuid4()),
            "tv": int(token_version),
        }
    )
    token = jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return token, expires_in


def decode_access_token(token: str, settings: Settings) -> AccessClaims:
    """Decode and validate an access token. Raises ``jwt.PyJWTError`` on failure."""
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        options={
            "require": ["exp", "iat", "nbf", "iss", "aud", "sub", "jti", "tv"],
        },
    )
    return AccessClaims(
        sub=uuid.UUID(payload["sub"]),
        exp=datetime.fromtimestamp(payload["exp"], tz=UTC),
        iat=datetime.fromtimestamp(payload["iat"], tz=UTC),
        iss=payload["iss"],
        aud=payload["aud"] if isinstance(payload["aud"], str) else payload["aud"][0],
        jti=str(payload["jti"]),
        token_version=int(payload["tv"]),
    )


def generate_refresh_token() -> tuple[str, str]:
    """Return ``(plaintext, sha256_hex)``. Only the hash is persisted."""
    plaintext = secrets.token_urlsafe(32)
    return plaintext, hash_refresh_token(plaintext)


def hash_refresh_token(plaintext: str) -> str:
    """SHA-256 hex digest of an opaque refresh token."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
