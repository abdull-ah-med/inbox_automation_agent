"""Microsoft Graph OAuth2 token management via MSAL.

Uses client credentials flow with scope https://graph.microsoft.com/.default,
which resolves to Mail.Read from the Entra app registration (application permission).

Token acquisition follows the official MSAL Python pattern:
acquire_token_silent first, then acquire_token_for_client on cache miss.

Token cache is an MSAL SerializableTokenCache persisted in Redis so multi-worker
processes share tokens across restarts.
"""

from __future__ import annotations

import asyncio
from typing import Any

import msal
import structlog
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.exceptions import GraphClientError
from app.core.redis_keys import MSAL_TOKEN_CACHE_KEY

logger = structlog.get_logger(__name__)

GRAPH_SCOPES: list[str] = ["https://graph.microsoft.com/.default"]


class GraphAuth:
    """Acquire Graph API access tokens via MSAL client credentials flow."""

    def __init__(self, settings: Settings, redis: Redis | None = None) -> None:
        self._settings = settings
        self._redis = redis
        self._cache = msal.SerializableTokenCache()
        self._app = self._build_msal_app(settings, self._cache)
        self._cache_loaded = False

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

    async def _ensure_cache_loaded(self) -> None:
        if self._cache_loaded or self._redis is None:
            self._cache_loaded = True
            return
        raw = await self._redis.get(MSAL_TOKEN_CACHE_KEY)
        if isinstance(raw, str) and raw:
            self._cache.deserialize(raw)
            logger.debug("graph_token_cache_loaded_from_redis")
        self._cache_loaded = True

    async def _persist_cache_if_changed(self) -> None:
        if self._redis is None or not self._cache.has_state_changed:
            return
        serialized = self._cache.serialize()
        await self._redis.set(MSAL_TOKEN_CACHE_KEY, serialized)
        logger.debug("graph_token_cache_persisted_to_redis")

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
        await self._ensure_cache_loaded()
        result = await asyncio.to_thread(self._acquire_token_sync)
        await self._persist_cache_if_changed()

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
