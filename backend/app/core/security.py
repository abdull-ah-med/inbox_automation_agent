"""Small crypto helpers for protecting secrets at rest (MSAL token cache).

Uses Fernet (AES-128-CBC + HMAC) from the ``cryptography`` package — already a
transitive dependency of MSAL. Key format: url-safe base64 32-byte key from
``Fernet.generate_key()``.

Official refs:
- https://cryptography.io/en/latest/fernet/
- https://learn.microsoft.com/en-us/entra/msal/python/advanced/msal-python-token-cache-serialization
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class CacheEncryptionError(ValueError):
    """Raised when MSAL cache encryption key is invalid or decrypt fails hard."""


def build_fernet(key: str) -> Fernet:
    """Construct a Fernet instance from a settings key string."""
    raw = key.strip()
    if not raw:
        raise CacheEncryptionError("Encryption key is empty")
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise CacheEncryptionError(
            "MSAL_CACHE_ENCRYPTION_KEY must be a Fernet key "
            "(url-safe base64-encoded 32-byte key from Fernet.generate_key())"
        ) from exc


def encrypt_cache_blob(plaintext: str, key: str) -> str:
    """Encrypt a UTF-8 MSAL serialize() string; returns utf-8 token text."""
    fernet = build_fernet(key)
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_cache_blob(ciphertext: str, key: str) -> str | None:
    """Decrypt a Fernet token. Returns None if ciphertext is not valid Fernet."""
    fernet = build_fernet(key)
    try:
        return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None
