"""Auth and at-rest crypto helpers.

Public surface keeps Fernet MSAL-cache helpers for existing callers, plus
password / JWT / CSRF utilities used by the web auth system.
"""

from app.core.security.cache_crypto import (
    CacheEncryptionError,
    build_fernet,
    decrypt_cache_blob,
    encrypt_cache_blob,
)
from app.core.security.csrf import mint_csrf, validate_csrf
from app.core.security.password import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.core.security.tokens import (
    AccessClaims,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)

__all__ = [
    "AccessClaims",
    "CacheEncryptionError",
    "build_fernet",
    "create_access_token",
    "decode_access_token",
    "decrypt_cache_blob",
    "encrypt_cache_blob",
    "generate_refresh_token",
    "hash_password",
    "hash_refresh_token",
    "mint_csrf",
    "needs_rehash",
    "validate_csrf",
    "validate_password_strength",
    "verify_password",
]
