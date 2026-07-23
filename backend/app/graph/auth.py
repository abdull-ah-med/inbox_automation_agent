"""Microsoft Graph OAuth2 token management via MSAL.

Uses client credentials flow with scope https://graph.microsoft.com/.default,
which resolves to Mail.Read from the Entra app registration (application permission).

Token acquisition follows the official MSAL Python pattern:
acquire_token_silent first, then acquire_token_for_client on cache miss.

Token cache is an MSAL SerializableTokenCache persisted in Redis so multi-worker
processes share tokens. Per MSAL distributed-cache guidance, reload from Redis
before every acquire and persist only when has_state_changed is True.
serialize() resets has_state_changed (do not clear it manually).

Outside local, the Redis blob is Fernet-encrypted (MSAL_CACHE_ENCRYPTION_KEY).
A Redis NX lock with owner token serializes load→acquire→persist across Uvicorn
workers so concurrent refreshes cannot last-write-wins overwrite each other.
Lock timeout fails closed — never mutate the shared blob without holding the lock.

See: https://learn.microsoft.com/en-us/entra/msal/python/advanced/msal-python-token-cache-serialization
"""

from __future__ import annotations

import asyncio
from typing import Any

import msal
import structlog
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.exceptions import GraphClientError
from app.core.redis_keys import (
    MSAL_TOKEN_CACHE_KEY,
    MSAL_TOKEN_CACHE_LOCK_KEY,
    MSAL_TOKEN_CACHE_LOCK_TTL_SECONDS,
)
from app.core.redis_lock import acquire_lock_with_retry, release_lock
from app.core.security import (
    CacheEncryptionError,
    decrypt_cache_blob,
    encrypt_cache_blob,
)

logger = structlog.get_logger(__name__)

GRAPH_SCOPES: list[str] = ["https://graph.microsoft.com/.default"]

_LOCK_RETRY_ATTEMPTS = 40
_LOCK_RETRY_DELAY_SECONDS = 0.05


class GraphAuth:
    """Acquire Graph API access tokens via MSAL client credentials flow."""

    def __init__(self, settings: Settings, redis: Redis | None = None) -> None:
        self._settings = settings
        self._redis = redis
        self._cache = msal.SerializableTokenCache()
        self._app = self._build_msal_app(settings, self._cache)

    @staticmethod
    def _build_msal_app(
        settings: Settings,
        cache: msal.SerializableTokenCache,
    ) -> msal.ConfidentialClientApplication:
        if not settings.graph_client_id or not settings.graph_tenant_id:
            raise GraphClientError(
                "GRAPH_CLIENT_ID and GRAPH_TENANT_ID must be configured for Graph auth"
            )
        if not settings.graph_client_secret:
            raise GraphClientError("GRAPH_CLIENT_SECRET must be configured for Graph auth")

        authority = f"https://login.microsoftonline.com/{settings.graph_tenant_id}"
        return msal.ConfidentialClientApplication(
            settings.graph_client_id,
            authority=authority,
            client_credential=settings.graph_client_secret,
            token_cache=cache,
        )

    def _encryption_key(self) -> str | None:
        key = self._settings.msal_cache_encryption_key.strip()
        return key or None

    def _decode_cache_payload(self, raw: str) -> str | None:
        """Decrypt if a key is configured; otherwise treat Redis value as plaintext."""
        key = self._encryption_key()
        if key is None:
            return raw
        try:
            decrypted = decrypt_cache_blob(raw, key)
        except CacheEncryptionError:
            logger.exception("graph_token_cache_encryption_key_invalid")
            raise GraphClientError("MSAL_CACHE_ENCRYPTION_KEY is invalid") from None
        if decrypted is not None:
            return decrypted
        # Migration path: plaintext blob written before encryption was enabled.
        logger.warning("graph_token_cache_plaintext_fallback")
        return raw

    def _encode_cache_payload(self, serialized: str) -> str:
        key = self._encryption_key()
        if key is None:
            return serialized
        try:
            return encrypt_cache_blob(serialized, key)
        except CacheEncryptionError as exc:
            raise GraphClientError(str(exc)) from exc

    async def _load_cache_from_redis(self) -> None:
        """Deserialize the latest shared MSAL cache before token acquisition."""
        if self._redis is None:
            return
        raw = await self._redis.get(MSAL_TOKEN_CACHE_KEY)
        if isinstance(raw, str) and raw:
            decoded = self._decode_cache_payload(raw)
            if decoded:
                self._cache.deserialize(decoded)
                logger.debug("graph_token_cache_loaded_from_redis")

    async def _persist_cache_if_changed(self) -> None:
        """Persist only when MSAL mutated the cache; serialize() clears has_state_changed."""
        if self._redis is None or not self._cache.has_state_changed:
            return
        serialized = self._cache.serialize()
        payload = self._encode_cache_payload(serialized)
        await self._redis.set(MSAL_TOKEN_CACHE_KEY, payload)
        logger.debug("graph_token_cache_persisted_to_redis", encrypted=bool(self._encryption_key()))

    async def _acquire_cache_lock(self) -> str | None:
        """NX lock so only one worker mutates the shared MSAL Redis blob at a time.

        Returns the owner token, or None when Redis is unset. Raises when Redis is
        configured but the lock cannot be acquired (fail closed — do not persist).
        """
        if self._redis is None:
            return None
        token = await acquire_lock_with_retry(
            self._redis,
            MSAL_TOKEN_CACHE_LOCK_KEY,
            ttl_seconds=MSAL_TOKEN_CACHE_LOCK_TTL_SECONDS,
            attempts=_LOCK_RETRY_ATTEMPTS,
            delay_seconds=_LOCK_RETRY_DELAY_SECONDS,
        )
        if token is None:
            logger.warning("graph_token_cache_lock_timeout")
            raise GraphClientError(
                "Could not acquire MSAL token cache lock — refusing to mutate shared cache"
            )
        return token

    async def _release_cache_lock(self, token: str | None) -> None:
        if self._redis is None or token is None:
            return
        await release_lock(self._redis, MSAL_TOKEN_CACHE_LOCK_KEY, token)

    def _acquire_token_sync(self) -> dict[str, Any]:
        result = self._app.acquire_token_silent(GRAPH_SCOPES, account=None)
        if result and "access_token" in result:
            logger.debug("graph_token_from_msal_cache")
            return dict(result)

        logger.info("graph_token_acquiring_via_client_credentials")
        result = self._app.acquire_token_for_client(scopes=GRAPH_SCOPES)
        if not result:
            raise GraphClientError("MSAL returned an empty token response")
        return dict(result)

    async def get_access_token(self) -> str:
        """Return a valid Graph API bearer token."""
        lock_token = await self._acquire_cache_lock()
        try:
            await self._load_cache_from_redis()
            result = await asyncio.to_thread(self._acquire_token_sync)
            await self._persist_cache_if_changed()
        finally:
            await self._release_cache_lock(lock_token)

        access_token = result.get("access_token")
        if isinstance(access_token, str) and access_token:
            logger.debug(
                "graph_token_acquired",
                expires_in=result.get("expires_in"),
            )
            return access_token

        error = result.get("error", "unknown_error")
        description = result.get("error_description", "No error description provided")
        logger.error("graph_token_acquisition_failed", error=error, description=description)
        raise GraphClientError(f"Failed to acquire Graph token: {error} — {description}")
