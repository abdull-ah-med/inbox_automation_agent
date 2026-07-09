"""Microsoft Graph HTTP client — READ-ONLY.

Mail.Read scope only. Application access policy restricts this app to target mailboxes.
No write, send, or modify methods will ever be added to this client.

Official docs:
- https://learn.microsoft.com/en-us/graph/api/message-get
- https://learn.microsoft.com/en-us/graph/api/user-list-messages
- https://learn.microsoft.com/en-us/graph/api/mailfolder-list-messages
- https://learn.microsoft.com/en-us/graph/api/subscription-post-subscriptions
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from app.core.exceptions import GraphClientError
from app.graph.auth import GraphAuth
from app.models.schemas.graph import (
    GraphMessageListSchema,
    GraphMessageSchema,
    GraphSubscriptionSchema,
)

logger = structlog.get_logger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_MESSAGE_SELECT = (
    "id,subject,bodyPreview,body,sender,from,toRecipients,ccRecipients,"
    "receivedDateTime,conversationId,isRead,hasAttachments,importance"
)
MAX_SUBSCRIPTION_MINUTES = 4230

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
    """Read-only Microsoft Graph client using httpx + MSAL auth."""

    def __init__(self, auth: GraphAuth, *, timeout: float = 30.0) -> None:
        self._auth = auth
        self._timeout = timeout

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        absolute_url: str | None = None,
    ) -> dict[str, Any] | None:
        token = await self._auth.get_access_token()
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if headers:
            request_headers.update(headers)

        url = absolute_url or f"{GRAPH_BASE_URL}{path}"
        logger.debug("graph_request", method=method, path=path or absolute_url)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=request_headers,
            )

        if response.status_code >= 400:
            logger.error(
                "graph_request_failed",
                method=method,
                path=path or absolute_url,
                status_code=response.status_code,
                body=response.text,
            )
            raise GraphClientError(
                f"Graph API {method} {path or absolute_url} failed "
                f"with status {response.status_code}: {response.text}"
            )

        if response.status_code == 204 or not response.content:
            return None
        payload = response.json()
        if not isinstance(payload, dict):
            raise GraphClientError(
                f"Graph API {method} {path or absolute_url} returned a non-object JSON payload"
            )
        return payload

    async def get_message(self, mailbox: str, message_id: str) -> GraphMessageSchema:
        """Fetch a single message by ID.

        GET /users/{mailbox}/messages/{message_id}
        """
        path = f"/users/{mailbox}/messages/{message_id}"
        data = await self._request(
            "GET",
            path,
            params={"$select": DEFAULT_MESSAGE_SELECT},
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        if not data:
            raise GraphClientError(f"Empty response fetching message {message_id}")
        return GraphMessageSchema.model_validate(data)

    async def list_messages(
        self,
        mailbox: str,
        *,
        folder: str = "inbox",
        filter_query: str | None = None,
        top: int = 10,
        select: str | None = None,
        orderby: str = "receivedDateTime desc",
        follow_next_link: bool = False,
    ) -> list[GraphMessageSchema]:
        """List messages in a mailbox folder.

        GET /users/{mailbox}/mailFolders('{folder}')/messages
        """
        path = f"/users/{mailbox}/mailFolders('{folder}')/messages"
        params: dict[str, str] = {
            "$top": str(top),
            "$orderby": orderby,
            "$select": select or DEFAULT_MESSAGE_SELECT,
        }
        if filter_query:
            params["$filter"] = filter_query

        data = await self._request(
            "GET",
            path,
            params=params,
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        page = GraphMessageListSchema.model_validate(data or {"value": []})
        messages = list(page.value)

        while follow_next_link and page.odata_next_link:
            data = await self._request(
                "GET",
                "",
                absolute_url=page.odata_next_link,
                headers={"Prefer": 'outlook.body-content-type="text"'},
            )
            page = GraphMessageListSchema.model_validate(data or {"value": []})
            messages.extend(page.value)

        return messages

    async def list_thread_messages(
        self,
        mailbox: str,
        conversation_id: str,
    ) -> list[GraphMessageSchema]:
        """Fetch all messages in a thread ordered by received time.

        GET /users/{mailbox}/messages?$filter=conversationId eq '{id}'
        """
        # Escape single quotes in OData string literals by doubling them.
        safe_conversation_id = conversation_id.replace("'", "''")
        filter_query = f"conversationId eq '{safe_conversation_id}'"
        path = f"/users/{mailbox}/messages"
        params: dict[str, str] = {
            "$filter": filter_query,
            "$orderby": "receivedDateTime asc",
            "$select": DEFAULT_MESSAGE_SELECT,
            "$top": "50",
        }

        data = await self._request(
            "GET",
            path,
            params=params,
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        page = GraphMessageListSchema.model_validate(data or {"value": []})
        messages = list(page.value)

        while page.odata_next_link:
            data = await self._request(
                "GET",
                "",
                absolute_url=page.odata_next_link,
                headers={"Prefer": 'outlook.body-content-type="text"'},
            )
            page = GraphMessageListSchema.model_validate(data or {"value": []})
            messages.extend(page.value)

        return messages

    async def create_subscription(
        self,
        mailbox: str,
        notification_url: str,
        client_state: str,
        *,
        expiration_minutes: int = MAX_SUBSCRIPTION_MINUTES,
    ) -> GraphSubscriptionSchema:
        """Create a change notification subscription for a mailbox inbox.

        POST /subscriptions
        Resource: users/{mailbox}/mailFolders('inbox')/messages
        Max lifetime for Outlook messages: 4,230 minutes.
        """
        if expiration_minutes > MAX_SUBSCRIPTION_MINUTES:
            raise GraphClientError(
                f"expiration_minutes cannot exceed {MAX_SUBSCRIPTION_MINUTES} "
                "for Outlook message subscriptions"
            )
        if len(client_state) > 128:
            raise GraphClientError("clientState must be 128 characters or fewer")

        expiration = datetime.now(UTC) + timedelta(minutes=expiration_minutes)
        body = {
            "changeType": "created",
            "notificationUrl": notification_url,
            "resource": f"users/{mailbox}/mailFolders('inbox')/messages",
            "expirationDateTime": expiration.isoformat().replace("+00:00", "Z"),
            "clientState": client_state,
        }
        data = await self._request("POST", "/subscriptions", json_body=body)
        if not data:
            raise GraphClientError("Empty response creating subscription")
        return GraphSubscriptionSchema.model_validate(data)

    async def renew_subscription(
        self,
        subscription_id: str,
        *,
        expiration_minutes: int = MAX_SUBSCRIPTION_MINUTES,
    ) -> GraphSubscriptionSchema:
        """Renew an existing change notification subscription.

        PATCH /subscriptions/{subscription_id}
        """
        if expiration_minutes > MAX_SUBSCRIPTION_MINUTES:
            raise GraphClientError(
                f"expiration_minutes cannot exceed {MAX_SUBSCRIPTION_MINUTES} "
                "for Outlook message subscriptions"
            )

        expiration = datetime.now(UTC) + timedelta(minutes=expiration_minutes)
        body = {
            "expirationDateTime": expiration.isoformat().replace("+00:00", "Z"),
        }
        # subscription_id is opaque; quote for path safety
        path = f"/subscriptions/{quote(subscription_id, safe='')}"
        data = await self._request("PATCH", path, json_body=body)
        if not data:
            raise GraphClientError(f"Empty response renewing subscription {subscription_id}")
        return GraphSubscriptionSchema.model_validate(data)

    async def delete_subscription(self, subscription_id: str) -> None:
        """Delete a change notification subscription.

        DELETE /subscriptions/{subscription_id}
        """
        path = f"/subscriptions/{quote(subscription_id, safe='')}"
        await self._request("DELETE", path)
