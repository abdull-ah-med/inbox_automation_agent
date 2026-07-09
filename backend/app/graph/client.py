"""Microsoft Graph HTTP client — READ-ONLY.

Mail.Read scope only. Application access policy restricts this app to target mailboxes.
No write, send, or modify methods will ever be added to this client.
"""

from typing import Any

ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    {
        "GET /users/{id}/messages",
        "GET /users/{id}/mailFolders/{id}/messages",
        "GET /users/{id}/messages/{id}",
        "POST /subscriptions",
        "PATCH /subscriptions/{id}",
        "DELETE /subscriptions/{id}",
        "GET /users/{id}/messages?$filter=...",
    }
)


class GraphClient:
    """Read-only Microsoft Graph client. Implementation added when Entra access is ready."""

    def __init__(self, access_token: str) -> None:
        self._access_token = access_token

    async def get_message(self, mailbox: str, message_id: str) -> dict[str, Any]:
        """Fetch a single message by ID."""
        raise NotImplementedError

    async def list_messages(
        self,
        mailbox: str,
        *,
        filter_query: str | None = None,
    ) -> list[dict[str, Any]]:
        """List messages for a mailbox, optionally filtered."""
        raise NotImplementedError

    async def list_thread_messages(
        self,
        mailbox: str,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch all messages in a thread ordered by received time."""
        raise NotImplementedError

    async def create_subscription(self, mailbox: str, notification_url: str) -> dict[str, Any]:
        """Create a change notification subscription for a mailbox inbox."""
        raise NotImplementedError

    async def renew_subscription(self, subscription_id: str) -> dict[str, Any]:
        """Renew an existing change notification subscription."""
        raise NotImplementedError

    async def delete_subscription(self, subscription_id: str) -> None:
        """Delete a change notification subscription."""
        raise NotImplementedError
