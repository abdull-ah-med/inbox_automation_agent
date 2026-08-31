"""Microsoft Graph HTTP client — READ-ONLY.

Mail.Read scope only. Application access policy restricts this app to target mailboxes.
No write, send, or modify methods will ever be added to this client.

Official docs:
- https://learn.microsoft.com/en-us/graph/api/message-get
- https://learn.microsoft.com/en-us/graph/api/user-list-messages
- https://learn.microsoft.com/en-us/graph/api/mailfolder-list-messages
- https://learn.microsoft.com/en-us/graph/api/subscription-post-subscriptions
- https://learn.microsoft.com/en-us/graph/throttling
- https://learn.microsoft.com/en-us/graph/paging (nextLink is opaque; still host-pin for SSRF)
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
import structlog

from app.core.exceptions import GraphClientError
from app.graph.auth import GraphAuth
from app.models.schemas.graph import (
    GraphAttachmentListSchema,
    GraphFileAttachmentSchema,
    GraphMessageListSchema,
    GraphMessageSchema,
    GraphSubscriptionSchema,
)

logger = structlog.get_logger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
_GRAPH_HOSTS = frozenset({"graph.microsoft.com"})
DEFAULT_MESSAGE_SELECT = (
    "id,subject,bodyPreview,body,uniqueBody,sender,from,toRecipients,ccRecipients,"
    "bccRecipients,receivedDateTime,conversationId,isRead,hasAttachments,importance,"
    "microsoft.graph.eventMessage/meetingMessageType,"
    "microsoft.graph.eventMessageResponse/responseType,internetMessageHeaders"
)
# Outlook View: body only, Prefer html (filtered — never outlook.allow-unsafe-html).
HTML_BODY_SELECT = "id,body,uniqueBody"
# Outlook message / event / contact subscriptions: 10,080 minutes (under 7 days).
# https://learn.microsoft.com/en-us/graph/api/resources/subscription
MAX_SUBSCRIPTION_MINUTES = 10_080
_ERROR_BODY_MAX_CHARS = 500
_AAD_UPN_PREFIX = "AAD-UPN:"
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

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
    return DEFAULT_RETRY_AFTER_SECONDS * float(2**attempt)


def _path_segment(value: str) -> str:
    """Percent-encode a single URL path segment (IDs may contain ``/``, ``@``, ``#``)."""
    return quote(value, safe="")


def outlook_subscription_user(mailbox: str) -> str:
    """User segment for Outlook change-notification ``resource`` values.

    Directory object IDs are used as-is. UPNs whose local-part is a GUID must
    be prefixed with ``AAD-UPN:`` so Exchange does not treat them as a mailbox
    GUID. Other UPNs are unchanged.

    https://learn.microsoft.com/en-us/graph/outlook-change-notifications-overview
    """
    value = mailbox.strip()
    if value.upper().startswith(_AAD_UPN_PREFIX):
        return f"{_AAD_UPN_PREFIX}{value[len(_AAD_UPN_PREFIX) :]}"
    if _UUID_RE.fullmatch(value):
        return value
    local, sep, _domain = value.partition("@")
    if sep and _UUID_RE.fullmatch(local):
        return f"{_AAD_UPN_PREFIX}{value}"
    return value


def _truncate_error_body(text: str) -> str:
    if len(text) <= _ERROR_BODY_MAX_CHARS:
        return text
    return text[:_ERROR_BODY_MAX_CHARS] + "…[truncated]"


def _assert_graph_absolute_url(url: str) -> str:
    """Validate nextLink host/scheme; return the URL unchanged (opaque per Graph docs)."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise GraphClientError(
            f"Refusing non-HTTPS Graph pagination URL (scheme={parsed.scheme!r})"
        )
    host = (parsed.hostname or "").lower()
    if host not in _GRAPH_HOSTS:
        raise GraphClientError(
            f"Refusing Graph pagination URL with host {host!r} (allowed: {sorted(_GRAPH_HOSTS)})"
        )
    return url


class GraphClient:
    """Read-only Microsoft Graph client using a long-lived httpx session + MSAL auth."""

    def __init__(self, auth: GraphAuth, *, timeout: float = 30.0) -> None:
        self._auth = auth
        self._timeout = timeout
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

        if absolute_url is not None:
            url = _assert_graph_absolute_url(absolute_url)
        else:
            url = f"{GRAPH_BASE_URL}{path}"
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

        if response is None:
            raise GraphClientError(
                f"Graph API {method} {path or absolute_url} produced no response"
            )

        if response.status_code >= 400:
            truncated = _truncate_error_body(response.text)
            logger.error(
                "graph_request_failed",
                method=method,
                path=path or absolute_url,
                status_code=response.status_code,
                error_body_truncated=truncated,
            )
            raise GraphClientError(
                f"Graph API {method} {path or absolute_url} failed "
                f"with status {response.status_code}: {truncated}"
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
        path = f"/users/{_path_segment(mailbox)}/messages/{_path_segment(message_id)}"
        data = await self._request(
            "GET",
            path,
            params={"$select": DEFAULT_MESSAGE_SELECT},
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        if not data:
            raise GraphClientError(f"Empty response fetching message {message_id}")
        return GraphMessageSchema.model_validate(data)

    async def get_message_html(self, mailbox: str, message_id: str) -> GraphMessageSchema:
        """Fetch a message body in Graph-filtered HTML for Outlook View.

        GET /users/{mailbox}/messages/{message_id}
        Prefer: outlook.body-content-type="html"
        Does not request outlook.allow-unsafe-html.
        """
        path = f"/users/{_path_segment(mailbox)}/messages/{_path_segment(message_id)}"
        data = await self._request(
            "GET",
            path,
            params={"$select": HTML_BODY_SELECT},
            headers={"Prefer": 'outlook.body-content-type="html"'},
        )
        if not data:
            raise GraphClientError(f"Empty response fetching HTML for message {message_id}")
        return GraphMessageSchema.model_validate(data)

    async def list_message_attachments(
        self,
        mailbox: str,
        message_id: str,
    ) -> list[GraphFileAttachmentSchema]:
        """List attachments for a message (including inline cid images).

        GET /users/{mailbox}/messages/{message_id}/attachments
        """
        path = f"/users/{_path_segment(mailbox)}/messages/{_path_segment(message_id)}/attachments"
        data = await self._request("GET", path)
        page = GraphAttachmentListSchema.model_validate(data or {"value": []})
        attachments = list(page.value)

        while page.odata_next_link:
            data = await self._request("GET", "", absolute_url=page.odata_next_link)
            page = GraphAttachmentListSchema.model_validate(data or {"value": []})
            attachments.extend(page.value)

        return attachments

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
        safe_folder = folder.replace("'", "''")
        path = f"/users/{_path_segment(mailbox)}/mailFolders('{safe_folder}')/messages"
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

        Do not combine conversationId $filter with $orderby=receivedDateTime —
        Graph returns InefficientFilter (400). Sort client-side instead.
        """
        safe_conversation_id = conversation_id.replace("'", "''")
        filter_query = f"conversationId eq '{safe_conversation_id}'"
        path = f"/users/{_path_segment(mailbox)}/messages"
        params: dict[str, str] = {
            "$filter": filter_query,
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

        messages.sort(key=lambda m: m.received_date_time or datetime.min.replace(tzinfo=UTC))
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
        folder: Literal["inbox", "sentitems"] = "inbox",
        expiration_minutes: int = MAX_SUBSCRIPTION_MINUTES,
    ) -> GraphSubscriptionSchema:
        """Create a change notification subscription for a mailbox folder.

        POST /subscriptions
        Resource: users/{user}/mailFolders('{folder}')/messages
        Well-known folder names: ``inbox``, ``sentitems`` (lowercase, no slash).
        Max lifetime for Outlook messages: 10,080 minutes (under seven days).

        lifecycleNotificationUrl cannot be added later via PATCH — must be set at create.
        https://learn.microsoft.com/en-us/graph/outlook-change-notifications-overview
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

        safe_folder = folder.replace("'", "''")
        expiration = datetime.now(UTC) + timedelta(minutes=expiration_minutes)
        user = outlook_subscription_user(mailbox)
        body = {
            "changeType": "created",
            "notificationUrl": notification_url,
            "lifecycleNotificationUrl": lifecycle_notification_url,
            "resource": f"users/{user}/mailFolders('{safe_folder}')/messages",
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
