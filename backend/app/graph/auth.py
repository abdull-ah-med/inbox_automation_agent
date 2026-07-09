"""Microsoft Graph OAuth2 token management via MSAL.

Uses client credentials flow with scope https://graph.microsoft.com/.default,
which resolves to Mail.Read from the Entra app registration (application permission).

Token acquisition follows the official MSAL Python pattern:
acquire_token_silent first, then acquire_token_for_client on cache miss.
"""

from __future__ import annotations

import asyncio
from typing import Any

import msal
import structlog

from app.core.config import Settings
from app.core.exceptions import GraphClientError

logger = structlog.get_logger(__name__)

GRAPH_SCOPES: list[str] = ["https://graph.microsoft.com/.default"]


class GraphAuth:
    """Acquire Graph API access tokens via MSAL client credentials flow."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._app = self._build_msal_app(settings)

    @staticmethod
    def _build_msal_app(settings: Settings) -> msal.ConfidentialClientApplication:
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
        )

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
        result = await asyncio.to_thread(self._acquire_token_sync)

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
