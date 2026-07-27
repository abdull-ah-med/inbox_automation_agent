"""Argon2id password hashing and strength policy.

OWASP Password Storage Cheat Sheet (Argon2id minimum):
  m=19456 (19 MiB), t=2, p=1
https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html

argon2-cffi docs:
https://argon2-cffi.readthedocs.io/en/stable/api.html
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# OWASP recommended minimum for Argon2id (19 MiB / 2 iterations / 1 lane).
_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)


def hash_password(plain: str) -> str:
    """Return an Argon2id-encoded password hash."""
    return _HASHER.hash(plain)


def verify_password(plain: str, encoded: str) -> bool:
    """Constant-time verify. Returns False on mismatch or invalid hash."""
    try:
        return _HASHER.verify(encoded, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(encoded: str) -> bool:
    """True when the stored hash uses weaker parameters than current policy."""
    try:
        return _HASHER.check_needs_rehash(encoded)
    except InvalidHashError:
        return True


def validate_password_strength(password: str) -> None:
    """Raise ValueError if password fails complexity rules.

    Policy: length 12..256, and at least 3 of 4 character classes
    (lower, upper, digit, symbol).
    """
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    if len(password) > 256:
        raise ValueError("Password must be at most 256 characters")

    classes = 0
    if any(c.islower() for c in password):
        classes += 1
    if any(c.isupper() for c in password):
        classes += 1
    if any(c.isdigit() for c in password):
        classes += 1
    if any(not c.isalnum() for c in password):
        classes += 1
    if classes < 3:
        raise ValueError(
            "Password must include at least 3 of: lowercase, uppercase, digit, symbol"
        )
