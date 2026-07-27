"""Double-submit CSRF tokens for cookie-authenticated mutating auth routes.

Uses HMAC-SHA256 with the JWT secret so tokens cannot be forged without the key.
The cookie is readable by JS; the SPA mirrors it into ``X-CSRF-Token``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets


def mint_csrf(secret: str) -> str:
    """Issue a CSRF token bound to the server secret."""
    nonce = secrets.token_urlsafe(32)
    sig = hmac.new(
        secret.encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{nonce}.{sig}"


def validate_csrf(secret: str, cookie_value: str | None, header_value: str | None) -> bool:
    """True when cookie and header match and the HMAC signature verifies."""
    if not cookie_value or not header_value:
        return False
    if not hmac.compare_digest(cookie_value, header_value):
        return False
    parts = cookie_value.split(".", 1)
    if len(parts) != 2:
        return False
    nonce, sig = parts
    expected = hmac.new(
        secret.encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sig, expected)
