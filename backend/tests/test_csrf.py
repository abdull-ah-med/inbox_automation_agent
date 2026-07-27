"""Unit tests for double-submit CSRF tokens."""

from __future__ import annotations

from app.core.security.csrf import mint_csrf, validate_csrf

SECRET = "csrf-test-secret-key-at-least-thirty-two-chars!!"


def test_mint_and_validate() -> None:
    """Minted token validates when cookie and header match."""
    token = mint_csrf(SECRET)
    assert validate_csrf(SECRET, token, token) is True


def test_mismatch_rejected() -> None:
    """Cookie/header mismatch fails."""
    token = mint_csrf(SECRET)
    assert validate_csrf(SECRET, token, token + "x") is False


def test_missing_rejected() -> None:
    """Missing cookie or header fails."""
    token = mint_csrf(SECRET)
    assert validate_csrf(SECRET, None, token) is False
    assert validate_csrf(SECRET, token, None) is False


def test_forged_signature_rejected() -> None:
    """Tampered HMAC signature fails."""
    token = mint_csrf(SECRET)
    nonce, _sig = token.split(".", 1)
    forged = f"{nonce}.{'0' * 64}"
    assert validate_csrf(SECRET, forged, forged) is False


def test_wrong_secret_rejected() -> None:
    """Token minted with one secret fails under another."""
    token = mint_csrf(SECRET)
    assert validate_csrf("other-secret-key-at-least-thirty-two!!", token, token) is False
