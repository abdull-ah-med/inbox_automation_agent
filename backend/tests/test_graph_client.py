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
    assert args[1] == f"{GRAPH_BASE_URL}/users/user@example.com/messages/msg-1"
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert 'outlook.body-content-type="text"' in kwargs["headers"]["Prefer"]


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
    assert "/users/user@example.com/mailFolders('inbox')/messages" in args[1]
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
    assert body["lifecycleNotificationUrl"] == (
        "https://example.com/webhooks/graph/lifecycle"
    )
    assert "/send" not in body["resource"]


@pytest.mark.asyncio
async def test_create_subscription_rejects_overlong_expiration(client: GraphClient) -> None:
    with pytest.raises(GraphClientError, match="4230"):
        await client.create_subscription(
            "user@example.com",
            "https://example.com/hook",
            "state",
            lifecycle_notification_url="https://example.com/lifecycle",
            expiration_minutes=5000,
        )


@pytest.mark.asyncio
async def test_list_thread_messages_filters_by_conversation_id(client: GraphClient) -> None:
    response = MagicMock()
    response.status_code = 200
    response.content = b'{"value":[]}'
    response.json.return_value = {
        "value": [
            {
                "id": "msg-1",
                "subject": "Hello",
                "conversationId": "conv'id",
                "bodyPreview": "Hi",
                "receivedDateTime": "2026-07-09T12:00:00Z",
            }
        ]
    }
    client._http.request = AsyncMock(return_value=response)

    messages = await client.list_thread_messages("user@example.com", "conv'id")

    assert len(messages) == 1
    assert messages[0].id == "msg-1"
    args, kwargs = client._http.request.call_args
    assert args[0] == "GET"
    assert args[1] == f"{GRAPH_BASE_URL}/users/user@example.com/messages"
    assert kwargs["params"]["$filter"] == "conversationId eq 'conv''id'"
    assert kwargs["params"]["$orderby"] == "receivedDateTime asc"


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
