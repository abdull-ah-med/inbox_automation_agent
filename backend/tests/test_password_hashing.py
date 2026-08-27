"""Unit tests for Argon2id password hashing."""

from __future__ import annotations

import pytest

from app.core.security.password import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)


def test_hash_roundtrip() -> None:
    """Hashed password verifies; wrong password does not."""
    encoded = hash_password("CorrectHorseBattery1!")
    assert verify_password("CorrectHorseBattery1!", encoded)
    assert not verify_password("wrong-password", encoded)


def test_needs_rehash_current_params() -> None:
    """Fresh hashes do not need rehash under current policy."""
    encoded = hash_password("CorrectHorseBattery1!")
    assert needs_rehash(encoded) is False


def test_verify_invalid_hash_returns_false() -> None:
    """Malformed hash returns False rather than raising."""
    assert verify_password("anything", "not-a-valid-argon2-hash") is False


@pytest.mark.parametrize(
    ("password", "ok"),
    [
        ("short", False),
        ("alllowercaseonly", False),
        ("NoDigitsOrSymbols", False),
        ("GoodPassword1!", True),
        ("another-Good-99", True),
    ],
)
def test_password_strength(password: str, ok: bool) -> None:
    """Complexity policy accepts strong passwords and rejects weak ones."""
    if ok:
        validate_password_strength(password)
    else:
        with pytest.raises(ValueError, match="Password must"):
            validate_password_strength(password)
