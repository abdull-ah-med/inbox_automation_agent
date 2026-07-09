"""Microsoft Graph OAuth2 token management via MSAL.

Uses client credentials flow with scope https://graph.microsoft.com/.default,
which resolves to Mail.Read from the Entra app registration.

Tokens are cached in Redis and refreshed before expiry when implemented.
"""

from app.core.config import Settings


class GraphAuth:
    """Acquire and refresh Graph API access tokens."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def get_access_token(self) -> str:
        """Return a valid Graph API bearer token."""
        raise NotImplementedError
