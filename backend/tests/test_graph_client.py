"""Unit tests for read-only GraphClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import GraphClientError
from app.graph.client import GRAPH_BASE_URL, GraphClient


@pytest.fixture
def auth() -> MagicMock:
    mock = MagicMock()
    mock.get_access_token = AsyncMock(return_value="test-token")
    return mock


@pytest.fixture
def client(auth: MagicMock) -> GraphClient:
    graph_client = GraphClient(auth)
    graph_client._http = AsyncMock()
    return graph_client


def test_graph_client_has_no_mail_write_methods() -> None:
    write_names = {
        "send_mail",
        "create_draft",
        "update_message",
        "delete_message",
        "reply",
        "forward",
        "move_message",
        "copy_message",
    }
    assert write_names.isdisjoint(set(dir(GraphClient)))


@pytest.mark.asyncio
async def test_get_message_calls_correct_url(client: GraphClient) -> None:
    response = MagicMock()
    response.status_code = 200
    response.content = b'{"id":"msg-1","subject":"Hello","conversationId":"c1"}'
    response.json.return_value = {
        "id": "msg-1",
        "subject": "Hello",
        "conversationId": "c1",
        "bodyPreview": "Hi",
        "receivedDateTime": "2026-07-09T12:00:00Z",
    }
    client._http.request = AsyncMock(return_value=response)

    message = await client.get_message("user@example.com", "msg-1")

    assert message.id == "msg-1"
    args, kwargs = client._http.request.call_args
    assert args[0] == "GET"
    assert args[1] == (f"{GRAPH_BASE_URL}/users/user%40example.com/messages/msg-1")
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert 'outlook.body-content-type="text"' in kwargs["headers"]["Prefer"]
    assert "uniqueBody" in kwargs["params"]["$select"]
    select = kwargs["params"]["$select"]
    assert "microsoft.graph.eventMessage/meetingMessageType" in select
    assert "microsoft.graph.eventMessageResponse/responseType" in select
    assert "internetMessageHeaders" in select


@pytest.mark.asyncio
async def test_get_message_html_prefers_html_and_selects_body_only(client: GraphClient) -> None:
    """Outlook View fetches filtered HTML — never Prefer text or allow-unsafe-html."""
    response = MagicMock()
    response.status_code = 200
    response.content = b"{}"
    response.json.return_value = {
        "id": "msg-html-1",
        "body": {"contentType": "html", "content": "<b>Invoice</b>"},
        "uniqueBody": {"contentType": "html", "content": "<b>Invoice</b>"},
    }
    client._http.request = AsyncMock(return_value=response)

    message = await client.get_message_html("user@example.com", "msg-html-1")

    assert message.body is not None
    assert message.body.content == "<b>Invoice</b>"
    args, kwargs = client._http.request.call_args
    assert args[0] == "GET"
    assert args[1] == (f"{GRAPH_BASE_URL}/users/user%40example.com/messages/msg-html-1")
    prefer = kwargs["headers"]["Prefer"]
    assert 'outlook.body-content-type="html"' in prefer
    assert "allow-unsafe-html" not in prefer
    select = kwargs["params"]["$select"]
    assert select == "id,body,uniqueBody"


@pytest.mark.asyncio
async def test_list_message_attachments_returns_inline_file(client: GraphClient) -> None:
    response = MagicMock()
    response.status_code = 200
    response.content = b"{}"
    response.json.return_value = {
        "value": [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "att-1",
                "name": "logo.png",
                "contentType": "image/png",
                "contentBytes": "iVBORw0KGgo=",
                "contentId": "logo@123",
                "isInline": True,
                "size": 12,
            }
        ]
    }
    client._http.request = AsyncMock(return_value=response)

    attachments = await client.list_message_attachments("user@example.com", "msg-1")

    assert len(attachments) == 1
    assert attachments[0].content_id == "logo@123"
    assert attachments[0].content_bytes == "iVBORw0KGgo="
    assert attachments[0].is_inline is True
    args, _kwargs = client._http.request.call_args
    assert args[0] == "GET"
    assert args[1] == (f"{GRAPH_BASE_URL}/users/user%40example.com/messages/msg-1/attachments")


@pytest.mark.asyncio
async def test_list_messages_uses_mailfolders_path(client: GraphClient) -> None:
    response = MagicMock()
    response.status_code = 200
    response.content = b'{"value":[]}'
    response.json.return_value = {"value": []}
    client._http.request = AsyncMock(return_value=response)

    messages = await client.list_messages("user@example.com", top=5)

    assert messages == []
    args, kwargs = client._http.request.call_args
    assert "/users/user%40example.com/mailFolders('inbox')/messages" in args[1]
    assert kwargs["params"]["$top"] == "5"


@pytest.mark.asyncio
async def test_create_subscription_includes_client_state_and_resource(
    client: GraphClient,
) -> None:
    response = MagicMock()
    response.status_code = 201
    response.content = b"{}"
    response.json.return_value = {
        "id": "sub-1",
        "resource": "users/user@example.com/mailFolders('inbox')/messages",
        "changeType": "created",
        "notificationUrl": "https://example.com/webhooks/graph/notifications",
        "expirationDateTime": "2026-07-12T00:00:00Z",
        "clientState": "secret-state",
    }
    client._http.request = AsyncMock(return_value=response)

    sub = await client.create_subscription(
        "user@example.com",
        "https://example.com/webhooks/graph/notifications",
        "secret-state",
        lifecycle_notification_url="https://example.com/webhooks/graph/lifecycle",
    )

    assert sub.id == "sub-1"
    args, kwargs = client._http.request.call_args
    assert args[0] == "POST"
    assert args[1] == f"{GRAPH_BASE_URL}/subscriptions"
    body = kwargs["json"]
    assert body["clientState"] == "secret-state"
    assert body["resource"] == "users/user@example.com/mailFolders('inbox')/messages"
    assert body["changeType"] == "created"
    assert body["lifecycleNotificationUrl"] == ("https://example.com/webhooks/graph/lifecycle")
    assert "/send" not in body["resource"]


@pytest.mark.asyncio
async def test_create_subscription_allows_outlook_seven_day_lifetime(
    client: GraphClient,
) -> None:
    """v1.0 Outlook message max is 10,080 minutes (under seven days).

    https://learn.microsoft.com/en-us/graph/api/resources/subscription
    """
    response = MagicMock()
    response.status_code = 201
    response.content = b"{}"
    response.json.return_value = {
        "id": "sub-7d",
        "resource": "users/user@example.com/mailFolders('inbox')/messages",
        "changeType": "created",
        "notificationUrl": "https://example.com/hook",
        "expirationDateTime": "2026-07-16T00:00:00Z",
        "clientState": "state",
    }
    client._http.request = AsyncMock(return_value=response)

    sub = await client.create_subscription(
        "user@example.com",
        "https://example.com/hook",
        "state",
        lifecycle_notification_url="https://example.com/lifecycle",
        expiration_minutes=10080,
    )

    assert sub.id == "sub-7d"
    client._http.request.assert_awaited()


@pytest.mark.asyncio
async def test_create_subscription_rejects_overlong_expiration(client: GraphClient) -> None:
    """One minute past the Outlook message maximum must fail closed."""
    with pytest.raises(GraphClientError, match="10080"):
        await client.create_subscription(
            "user@example.com",
            "https://example.com/hook",
            "state",
            lifecycle_notification_url="https://example.com/lifecycle",
            expiration_minutes=10081,
        )


@pytest.mark.asyncio
async def test_create_subscription_prefixes_guid_shaped_upn(client: GraphClient) -> None:
    """Microsoft Example 2: UPN that begins with a GUID needs AAD-UPN:.

    https://learn.microsoft.com/en-us/graph/outlook-change-notifications-overview
    """
    response = MagicMock()
    response.status_code = 201
    response.content = b"{}"
    mailbox = "3f8c2a71-6d45-4e9b-a237-81c5f0d762ae@contoso.com"
    response.json.return_value = {
        "id": "sub-guid",
        "resource": (f"users/AAD-UPN:{mailbox}/mailFolders('inbox')/messages"),
        "changeType": "created",
        "notificationUrl": "https://example.com/hook",
        "expirationDateTime": "2026-07-12T00:00:00Z",
        "clientState": "state",
    }
    client._http.request = AsyncMock(return_value=response)

    await client.create_subscription(
        mailbox,
        "https://example.com/hook",
        "state",
        lifecycle_notification_url="https://example.com/lifecycle",
    )

    body = client._http.request.await_args.kwargs["json"]
    assert body["resource"] == (
        "users/AAD-UPN:3f8c2a71-6d45-4e9b-a237-81c5f0d762ae@contoso.com"
        "/mailFolders('inbox')/messages"
    )


@pytest.mark.asyncio
async def test_create_subscription_uses_directory_oid_as_user_segment(
    client: GraphClient,
) -> None:
    """Bare Entra object IDs stay unprefixed so Exchange maps to one mailbox."""
    response = MagicMock()
    response.status_code = 201
    response.content = b"{}"
    oid = "bb8775a4-4d8c-42cf-a1d4-4d58c2bb668f"
    response.json.return_value = {
        "id": "sub-oid",
        "resource": f"users/{oid}/mailFolders('inbox')/messages",
        "changeType": "created",
        "notificationUrl": "https://example.com/hook",
        "expirationDateTime": "2026-07-12T00:00:00Z",
        "clientState": "state",
    }
    client._http.request = AsyncMock(return_value=response)

    await client.create_subscription(
        oid,
        "https://example.com/hook",
        "state",
        lifecycle_notification_url="https://example.com/lifecycle",
    )

    body = client._http.request.await_args.kwargs["json"]
    assert body["resource"] == (f"users/{oid}/mailFolders('inbox')/messages")


@pytest.mark.asyncio
async def test_list_thread_messages_filters_by_conversation_id(client: GraphClient) -> None:
    response = MagicMock()
    response.status_code = 200
    response.content = b'{"value":[]}'
    response.json.return_value = {
        "value": [
            {
                "id": "msg-2",
                "subject": "Re: Hello",
                "conversationId": "conv'id",
                "bodyPreview": "Later",
                "receivedDateTime": "2026-07-09T13:00:00Z",
            },
            {
                "id": "msg-1",
                "subject": "Hello",
                "conversationId": "conv'id",
                "bodyPreview": "Hi",
                "receivedDateTime": "2026-07-09T12:00:00Z",
            },
        ]
    }
    client._http.request = AsyncMock(return_value=response)

    messages = await client.list_thread_messages("user@example.com", "conv'id")

    assert [m.id for m in messages] == ["msg-1", "msg-2"]
    args, kwargs = client._http.request.call_args
    assert args[0] == "GET"
    assert args[1] == f"{GRAPH_BASE_URL}/users/user%40example.com/messages"
    assert kwargs["params"]["$filter"] == "conversationId eq 'conv''id'"
    assert "$orderby" not in kwargs["params"]


@pytest.mark.asyncio
async def test_create_subscription_rejects_overlong_client_state(client: GraphClient) -> None:
    with pytest.raises(GraphClientError, match="128"):
        await client.create_subscription(
            "user@example.com",
            "https://example.com/hook",
            "x" * 129,
            lifecycle_notification_url="https://example.com/lifecycle",
        )


@pytest.mark.asyncio
async def test_list_subscriptions_calls_get(client: GraphClient) -> None:
    response = MagicMock()
    response.status_code = 200
    response.content = b"{}"
    response.json.return_value = {
        "value": [
            {
                "id": "sub-1",
                "resource": "users/user@example.com/mailFolders('inbox')/messages",
                "changeType": "created",
                "notificationUrl": "https://example.com/webhooks/graph/notifications",
                "lifecycleNotificationUrl": "https://example.com/webhooks/graph/lifecycle",
                "expirationDateTime": "2026-07-12T00:00:00Z",
                "clientState": "secret-state",
            }
        ]
    }
    client._http.request = AsyncMock(return_value=response)

    subs = await client.list_subscriptions()

    assert len(subs) == 1
    assert subs[0].id == "sub-1"
    assert subs[0].lifecycle_notification_url is not None
    args, _kwargs = client._http.request.call_args
    assert args[0] == "GET"
    assert args[1] == f"{GRAPH_BASE_URL}/subscriptions"


@pytest.mark.asyncio
async def test_renew_and_delete_subscription(client: GraphClient) -> None:
    renew_response = MagicMock()
    renew_response.status_code = 200
    renew_response.content = b"{}"
    renew_response.json.return_value = {
        "id": "sub-1",
        "resource": "users/user@example.com/mailFolders('inbox')/messages",
        "changeType": "created",
        "notificationUrl": "https://example.com/webhooks/graph/notifications",
        "expirationDateTime": "2026-07-14T00:00:00Z",
    }

    delete_response = MagicMock()
    delete_response.status_code = 204
    delete_response.content = b""

    client._http.request = AsyncMock(side_effect=[renew_response, delete_response])

    renewed = await client.renew_subscription("sub-1")
    await client.delete_subscription("sub-1")

    assert renewed.id == "sub-1"
    assert client._http.request.await_count == 2
    renew_call = client._http.request.await_args_list[0]
    assert renew_call.args[0] == "PATCH"
    delete_call = client._http.request.await_args_list[1]
    assert delete_call.args[0] == "DELETE"


@pytest.mark.asyncio
async def test_http_error_raises_graph_client_error(client: GraphClient) -> None:
    response = MagicMock()
    response.status_code = 403
    response.content = b'{"error":"forbidden"}'
    response.text = '{"error":"forbidden"}'
    client._http.request = AsyncMock(return_value=response)

    with pytest.raises(GraphClientError, match="403"):
        await client.get_message("user@example.com", "msg-1")


@pytest.mark.asyncio
async def test_429_retries_using_retry_after_header(client: GraphClient) -> None:
    throttled = MagicMock()
    throttled.status_code = 429
    throttled.headers = {"Retry-After": "0"}
    throttled.content = b'{"error":"throttled"}'
    throttled.text = '{"error":"throttled"}'

    ok = MagicMock()
    ok.status_code = 200
    ok.content = b'{"id":"msg-1","conversationId":"c1"}'
    ok.json.return_value = {
        "id": "msg-1",
        "subject": "Hello",
        "conversationId": "c1",
        "receivedDateTime": "2026-07-09T12:00:00Z",
    }

    client._http.request = AsyncMock(side_effect=[throttled, ok])

    with patch("app.graph.client.asyncio.sleep", new_callable=AsyncMock) as sleep:
        message = await client.get_message("user@example.com", "msg-1")

    assert message.id == "msg-1"
    assert client._http.request.await_count == 2
    sleep.assert_awaited_once_with(0.0)


@pytest.mark.asyncio
async def test_429_exhausted_retries_raises(client: GraphClient) -> None:
    throttled = MagicMock()
    throttled.status_code = 429
    throttled.headers = {"Retry-After": "0"}
    throttled.content = b'{"error":"throttled"}'
    throttled.text = '{"error":"throttled"}'
    client._http.request = AsyncMock(return_value=throttled)

    with (
        patch("app.graph.client.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(GraphClientError, match="429"),
    ):
        await client.get_message("user@example.com", "msg-1")

    assert client._http.request.await_count == 4  # 1 initial + 3 retries


@pytest.mark.asyncio
async def test_http_client_constructed_once_and_reused_across_requests(auth: MagicMock) -> None:
    """Regression: per-request AsyncClient destroyed pooling (DNS/TCP/TLS each call)."""
    ok = MagicMock()
    ok.status_code = 200
    ok.content = b'{"id":"msg-1","conversationId":"c1"}'
    ok.json.return_value = {
        "id": "msg-1",
        "subject": "Hello",
        "conversationId": "c1",
        "receivedDateTime": "2026-07-09T12:00:00Z",
    }
    mock_http = AsyncMock()
    mock_http.request = AsyncMock(return_value=ok)

    with patch("app.graph.client.httpx.AsyncClient", return_value=mock_http) as ctor:
        graph_client = GraphClient(auth)
        await graph_client.get_message("user@example.com", "msg-1")
        await graph_client.get_message("user@example.com", "msg-2")

    ctor.assert_called_once()
    assert mock_http.request.await_count == 2


@pytest.mark.asyncio
async def test_503_retries_like_429(client: GraphClient) -> None:
    unavailable = MagicMock()
    unavailable.status_code = 503
    unavailable.headers = {"Retry-After": "0"}
    unavailable.content = b'{"error":"unavailable"}'
    unavailable.text = '{"error":"unavailable"}'

    ok = MagicMock()
    ok.status_code = 200
    ok.content = b'{"id":"msg-1","conversationId":"c1"}'
    ok.json.return_value = {
        "id": "msg-1",
        "subject": "Hello",
        "conversationId": "c1",
        "receivedDateTime": "2026-07-09T12:00:00Z",
    }
    client._http.request = AsyncMock(side_effect=[unavailable, ok])

    with patch("app.graph.client.asyncio.sleep", new_callable=AsyncMock) as sleep:
        message = await client.get_message("user@example.com", "msg-1")

    assert message.id == "msg-1"
    assert client._http.request.await_count == 2
    sleep.assert_awaited_once_with(0.0)


@pytest.mark.asyncio
async def test_aclose_closes_http_client(auth: MagicMock) -> None:
    graph_client = GraphClient(auth)
    mock_http = AsyncMock()
    graph_client._http = mock_http
    await graph_client.aclose()
    mock_http.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_next_link_rejects_non_https_host(client: GraphClient) -> None:
    first = MagicMock()
    first.status_code = 200
    first.content = b"{}"
    first.json.return_value = {
        "value": [],
        "@odata.nextLink": "http://evil.example/steal",
    }
    client._http.request = AsyncMock(return_value=first)

    with pytest.raises(GraphClientError, match=r"non-HTTPS|Refusing"):
        await client.list_messages("user@example.com", follow_next_link=True)


@pytest.mark.asyncio
async def test_next_link_rejects_wrong_host(client: GraphClient) -> None:
    first = MagicMock()
    first.status_code = 200
    first.content = b"{}"
    first.json.return_value = {
        "value": [],
        "@odata.nextLink": "https://evil.example/v1.0/me/messages",
    }
    client._http.request = AsyncMock(return_value=first)

    with pytest.raises(GraphClientError, match="host"):
        await client.list_messages("user@example.com", follow_next_link=True)


@pytest.mark.asyncio
async def test_next_link_accepts_graph_host_unchanged(client: GraphClient) -> None:
    next_url = "https://graph.microsoft.com/v1.0/users/user%40example.com/messages?$skiptoken=abc"
    first = MagicMock()
    first.status_code = 200
    first.content = b"{}"
    first.json.return_value = {"value": [], "@odata.nextLink": next_url}
    second = MagicMock()
    second.status_code = 200
    second.content = b"{}"
    second.json.return_value = {"value": []}
    client._http.request = AsyncMock(side_effect=[first, second])

    await client.list_messages("user@example.com", follow_next_link=True)

    assert client._http.request.await_count == 2
    assert client._http.request.await_args_list[1].args[1] == next_url


@pytest.mark.asyncio
async def test_get_message_encodes_slash_in_id(client: GraphClient) -> None:
    response = MagicMock()
    response.status_code = 200
    response.content = b'{"id":"a/b","conversationId":"c1"}'
    response.json.return_value = {
        "id": "a/b",
        "subject": "Hello",
        "conversationId": "c1",
        "receivedDateTime": "2026-07-09T12:00:00Z",
    }
    client._http.request = AsyncMock(return_value=response)

    await client.get_message("user@example.com", "a/b")

    assert "/messages/a%2Fb" in client._http.request.call_args.args[1]


@pytest.mark.asyncio
async def test_error_body_truncated_in_exception(client: GraphClient) -> None:
    response = MagicMock()
    response.status_code = 500
    response.content = b"x"
    response.text = "Z" * 2000
    client._http.request = AsyncMock(return_value=response)

    with pytest.raises(GraphClientError) as exc_info:
        await client.get_message("user@example.com", "msg-1")

    assert len(str(exc_info.value)) < 800
    assert "truncated" in str(exc_info.value)
