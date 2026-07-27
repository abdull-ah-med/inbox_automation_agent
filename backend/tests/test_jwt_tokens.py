"""Unit tests for JWT access tokens and refresh token hashing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.security.tokens import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="x" * 64,
        jwt_issuer="inbox-triage-automation",
        jwt_audience="inbox-triage-web",
        access_token_ttl_seconds=900,
    )


def test_create_and_decode(auth_settings: Settings) -> None:
    """Issued access token decodes with expected subject."""
    user_id = uuid.uuid4()
    token, expires_in = create_access_token(user_id, auth_settings, token_version=3)
    assert expires_in == 900
    claims = decode_access_token(token, auth_settings)
    assert claims.sub == user_id
    assert claims.iss == auth_settings.jwt_issuer
    assert claims.aud == auth_settings.jwt_audience
    assert claims.token_version == 3
    assert claims.jti


def test_wrong_secret_rejected(auth_settings: Settings) -> None:
    """Token signed with a different secret fails verification."""
    user_id = uuid.uuid4()
    token, _ = create_access_token(user_id, auth_settings)
    other = auth_settings.model_copy(update={"jwt_secret": "y" * 64})
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token, other)


def test_wrong_audience_rejected(auth_settings: Settings) -> None:
    """Audience mismatch raises."""
    user_id = uuid.uuid4()
    token, _ = create_access_token(user_id, auth_settings)
    other = auth_settings.model_copy(update={"jwt_audience": "other-aud"})
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token, other)


def test_expired_token_rejected(auth_settings: Settings) -> None:
    """Expired token raises ExpiredSignatureError."""
    short = auth_settings.model_copy(update={"access_token_ttl_seconds": 1})
    user_id = uuid.uuid4()
    now = datetime.now(UTC) - timedelta(hours=1)
    payload = {
        "sub": str(user_id),
        "iss": short.jwt_issuer,
        "aud": short.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=1),
        "jti": str(uuid.uuid4()),
        "tv": 0,
    }
    token = jwt.encode(payload, short.jwt_secret, algorithm=short.jwt_algorithm)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, short)


def test_tampered_token_rejected(auth_settings: Settings) -> None:
    """Mutated payload fails signature check."""
    user_id = uuid.uuid4()
    token, _ = create_access_token(user_id, auth_settings)
    parts = token.split(".")
    # Flip a character in the payload segment.
    mutated = parts[0] + "." + parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B") + "." + parts[2]
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(mutated, auth_settings)


def test_refresh_token_hash_stable() -> None:
    """Same plaintext always hashes to the same SHA-256 hex."""
    plaintext, digest = generate_refresh_token()
    assert len(plaintext) >= 32
    assert digest == hash_refresh_token(plaintext)
    assert len(digest) == 64
