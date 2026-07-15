"""Microsoft Graph HTTP client — READ-ONLY.

Mail.Read scope only. Application access policy restricts this app to target mailboxes.
No write, send, or modify methods will ever be added to this client.

Official docs:
- https://learn.microsoft.com/en-us/graph/api/message-get
- https://learn.microsoft.com/en-us/graph/api/user-list-messages
- https://learn.microsoft.com/en-us/graph/api/mailfolder-list-messages
- https://learn.microsoft.com/en-us/graph/api/subscription-post-subscriptions
- https://learn.microsoft.com/en-us/graph/throttling
"""

from __future__ import annotations

import asyncio
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

# Graph throttling: honor Retry-After; retry up to 3 times after the first failure.
# https://learn.microsoft.com/en-us/graph/throttling
MAX_THROTTLE_RETRIES = 3
DEFAULT_RETRY_AFTER_SECONDS = 5.0
_THROTTLE_STATUS_CODES = frozenset({429, 503})


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    """Parse Retry-After (seconds). Fall back to exponential backoff when absent."""
    raw = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if raw is not None:
        try:
            parsed = float(str(raw).strip())
        except ValueError:
            logger.warning("graph_retry_after_unparseable", retry_after=raw)
        else:
            return parsed if parsed > 0.0 else 0.0
    backoff = DEFAULT_RETRY_AFTER_SECONDS * float(2**attempt)
    return backoff


class GraphClient:
    """Read-only Microsoft Graph client using a long-lived httpx session + MSAL auth."""

    def __init__(self, auth: GraphAuth, *, timeout: float = 30.0) -> None:
        self._auth = auth
        self._timeout = timeout
        # Long-lived client for connection pooling (httpx docs: do not create per request).
        self._http = httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

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
        }
        if headers:
            request_headers.update(headers)

        url = absolute_url or f"{GRAPH_BASE_URL}{path}"
        logger.debug("graph_request", method=method, path=path or absolute_url)

        response: httpx.Response | None = None
        for attempt in range(MAX_THROTTLE_RETRIES + 1):
            response = await self._http.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=request_headers,
            )
            if response.status_code not in _THROTTLE_STATUS_CODES:
                break
            if attempt >= MAX_THROTTLE_RETRIES:
                break
            delay = _retry_after_seconds(response, attempt)
            logger.warning(
                "graph_request_throttled",
                method=method,
                path=path or absolute_url,
                status_code=response.status_code,
                retry_after_seconds=delay,
                attempt=attempt + 1,
                max_retries=MAX_THROTTLE_RETRIES,
            )
            await asyncio.sleep(delay)

        assert response is not None

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

    async def list_subscriptions(self) -> list[GraphSubscriptionSchema]:
        """List all change-notification subscriptions for this app.

        GET /subscriptions
        """
        data = await self._request("GET", "/subscriptions")
        raw_value = (data or {}).get("value", [])
        subscriptions = [
            GraphSubscriptionSchema.model_validate(item)
            for item in raw_value
            if isinstance(item, dict)
        ]
        next_link = (data or {}).get("@odata.nextLink")
        while isinstance(next_link, str) and next_link:
            page_data = await self._request("GET", "", absolute_url=next_link)
            page_value = (page_data or {}).get("value", [])
            subscriptions.extend(
                GraphSubscriptionSchema.model_validate(item)
                for item in page_value
                if isinstance(item, dict)
            )
            next_link = (page_data or {}).get("@odata.nextLink")
        return subscriptions

    async def create_subscription(
        self,
        mailbox: str,
        notification_url: str,
        client_state: str,
        *,
        lifecycle_notification_url: str,
        expiration_minutes: int = MAX_SUBSCRIPTION_MINUTES,
    ) -> GraphSubscriptionSchema:
        """Create a change notification subscription for a mailbox inbox.

        POST /subscriptions
        Resource: users/{mailbox}/mailFolders('inbox')/messages
        Max lifetime for Outlook messages: 4,230 minutes.

        lifecycleNotificationUrl cannot be added later via PATCH — must be set at create.
        """
        if expiration_minutes > MAX_SUBSCRIPTION_MINUTES:
            raise GraphClientError(
                f"expiration_minutes cannot exceed {MAX_SUBSCRIPTION_MINUTES} "
                "for Outlook message subscriptions"
            )
        if len(client_state) > 128:
            raise GraphClientError("clientState must be 128 characters or fewer")
        if not lifecycle_notification_url.strip():
            raise GraphClientError("lifecycle_notification_url is required")

        expiration = datetime.now(UTC) + timedelta(minutes=expiration_minutes)
        body = {
            "changeType": "created",
            "notificationUrl": notification_url,
            "lifecycleNotificationUrl": lifecycle_notification_url,
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
