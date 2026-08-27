"""SentItems subscription + outbound notification reliability tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.graph.auth import GraphAuth
from app.graph.client import GraphClient
from app.models.schemas.graph import (
    GraphMessageSchema,
    GraphNotificationItemSchema,
    GraphSubscriptionSchema,
    IngestResultSchema,
)
from app.services import ingestion_service, subscription_service


def _settings() -> Settings:
    return Settings(
        graph_notification_url="https://example.com/webhooks/graph/notifications",
        graph_lifecycle_url="https://example.com/webhooks/graph/lifecycle",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )


def _sub(
    *,
    sub_id: str = "sub-1",
    folder: str = "inbox",
    expiration: datetime | None = None,
) -> GraphSubscriptionSchema:
    return GraphSubscriptionSchema.model_validate(
        {
            "id": sub_id,
            "resource": f"users/user@example.com/mailFolders('{folder}')/messages",
            "changeType": "created",
            "notificationUrl": "https://example.com/webhooks/graph/notifications",
            "lifecycleNotificationUrl": "https://example.com/webhooks/graph/lifecycle",
            "expirationDateTime": (expiration or datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "clientState": "secret",
        }
    )


@pytest.fixture
def auth() -> MagicMock:
    mock = MagicMock(spec=GraphAuth)
    mock.get_access_token = AsyncMock(return_value="token")
    return mock


@pytest.fixture
def client(auth: MagicMock) -> GraphClient:
    return GraphClient(auth)


@pytest.mark.asyncio
async def test_create_subscription_folder_param(client: GraphClient) -> None:
    response = MagicMock()
    response.status_code = 201
    response.content = b"{}"
    response.json.return_value = {
        "id": "sub-sent",
        "resource": "users/user@example.com/mailFolders('sentitems')/messages",
        "changeType": "created",
        "notificationUrl": "https://example.com/webhooks/graph/notifications",
        "expirationDateTime": "2026-07-12T00:00:00Z",
        "clientState": "secret",
    }
    client._http.request = AsyncMock(return_value=response)

    sub = await client.create_subscription(
        "user@example.com",
        "https://example.com/webhooks/graph/notifications",
        "secret",
        lifecycle_notification_url="https://example.com/webhooks/graph/lifecycle",
        folder="sentitems",
    )

    assert sub.id == "sub-sent"
    body = client._http.request.call_args.kwargs["json"]
    assert body["resource"] == "users/user@example.com/mailFolders('sentitems')/messages"
    assert "/send" not in body["resource"]


@pytest.mark.asyncio
async def test_create_subscription_reuses_httpx_client_across_folders(
    auth: MagicMock,
) -> None:
    mock_http = MagicMock()
    mock_http.request = AsyncMock(
        return_value=MagicMock(
            status_code=201,
            content=b"{}",
            json=MagicMock(
                return_value={
                    "id": "sub-1",
                    "resource": "users/user@example.com/mailFolders('inbox')/messages",
                    "changeType": "created",
                    "notificationUrl": "https://example.com/hook",
                    "expirationDateTime": "2026-07-12T00:00:00Z",
                    "clientState": "secret",
                }
            ),
            headers={},
            text="",
        )
    )
    mock_http.aclose = AsyncMock()

    with patch("app.graph.client.httpx.AsyncClient", return_value=mock_http) as ctor:
        graph = GraphClient(auth)
        await graph.create_subscription(
            "user@example.com",
            "https://example.com/hook",
            "secret",
            lifecycle_notification_url="https://example.com/lifecycle",
            folder="inbox",
        )
        await graph.create_subscription(
            "user@example.com",
            "https://example.com/hook",
            "secret",
            lifecycle_notification_url="https://example.com/lifecycle",
            folder="sentitems",
        )

    assert ctor.call_count == 1
    assert mock_http.request.await_count == 2
    await graph.aclose()


@pytest.mark.asyncio
async def test_reconcile_all_creates_inbox_and_sentitems() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.eval = AsyncMock(return_value=1)
    redis.delete = AsyncMock(return_value=1)
    graph_client = MagicMock()
    graph_client.list_subscriptions = AsyncMock(return_value=[])
    graph_client.create_subscription = AsyncMock(
        side_effect=[
            _sub(sub_id="sub-inbox", folder="inbox"),
            _sub(sub_id="sub-sent", folder="sentitems"),
        ]
    )

    await subscription_service.reconcile_all_mailboxes(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
    )

    assert graph_client.create_subscription.await_count == 2
    folders = [
        call.kwargs.get("folder", "inbox")
        for call in graph_client.create_subscription.await_args_list
    ]
    assert folders == ["inbox", "sentitems"]


@pytest.mark.asyncio
async def test_reconcile_does_not_delete_other_folder() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    inbox = _sub(sub_id="sub-inbox", folder="inbox")
    sent = _sub(sub_id="sub-sent", folder="sentitems")
    graph_client = MagicMock()
    graph_client.list_subscriptions = AsyncMock(return_value=[inbox, sent])
    graph_client.create_subscription = AsyncMock()
    graph_client.delete_subscription = AsyncMock()

    result = await subscription_service.reconcile_mailbox_subscription(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        mailbox="user@example.com",
        folder="inbox",
    )

    assert result is not None
    assert result.id == "sub-inbox"
    graph_client.delete_subscription.assert_not_awaited()
    graph_client.create_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_lifecycle_sentitems_removed_reconciles_only_sentitems() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"subscription_id":"sub-sent"}')
    redis.delete = AsyncMock(return_value=1)
    redis.set = AsyncMock(return_value=True)
    redis.eval = AsyncMock(return_value=1)
    graph_client = MagicMock()
    graph_client.list_subscriptions = AsyncMock(return_value=[])
    graph_client.create_subscription = AsyncMock(
        return_value=_sub(sub_id="sub-sent-2", folder="sentitems")
    )

    item = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-sent",
            "clientState": "secret",
            "lifecycleEvent": "subscriptionRemoved",
            "resource": "users/user@example.com/mailFolders('sentitems')/messages",
        }
    )

    await subscription_service.handle_lifecycle_event(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        notification=item,
    )

    create_kwargs = graph_client.create_subscription.await_args.kwargs
    assert create_kwargs["folder"] == "sentitems"


@pytest.mark.asyncio
async def test_lifecycle_sentitems_missed_polls_outbound_only() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"subscription_id":"sub-sent"}')
    graph_client = MagicMock()
    poll_fn = AsyncMock()

    item = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-sent",
            "clientState": "secret",
            "lifecycleEvent": "missed",
            "resource": "users/user@example.com/mailFolders('sentitems')/messages",
        }
    )

    await subscription_service.handle_lifecycle_event(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        notification=item,
        poll_mailbox_fn=poll_fn,
    )

    poll_fn.assert_awaited_once()
    assert poll_fn.await_args.kwargs["folders"] == ("sentitems",)
    assert poll_fn.await_args.kwargs["outbound_only"] is True
    assert poll_fn.await_args.kwargs["lookback_override"] is not None


@pytest.mark.asyncio
async def test_poison_sentitems_notification_returns_202() -> None:
    """Unparseable SentItems notification payloads must ACK 202 (Graph delivery contract)."""
    from fastapi import FastAPI

    from app.api.webhooks.graph import router as graph_router
    from app.core.dependencies import get_redis, get_settings

    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=1)
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhooks/graph/notifications",
            content=b"{not-json",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 202
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_outbound_notification_idempotent() -> None:
    mailbox = "user@example.com"
    message_id = "AAMkAG-sent-dup"
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=["processing", "completed"])
    redis.set = AsyncMock(return_value=False)
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock()

    # First call: in_flight while another worker holds the claim
    result1 = await ingestion_service.handle_outbound_notification(
        session=session,
        redis=redis,
        graph_client=graph_client,
        mailbox=mailbox,
        message_id=message_id,
    )
    assert result1.status == "in_flight"
    graph_client.get_message.assert_not_awaited()

    # Second call: already completed and nothing in DB to heal
    with patch(
        "app.services.ingestion_service.message_repo.get_by_graph_id",
        AsyncMock(return_value=None),
    ):
        result2 = await ingestion_service.handle_outbound_notification(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox=mailbox,
            message_id=message_id,
        )
    assert result2.status == "duplicate"
    graph_client.get_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbound_completed_dedup_heals_unresolved_shared_send() -> None:
    """EC2 Brittany case: inbound triage completed Redis dedup without resolve.

    Sent Items backfill then hits claim=completed and must still RESOLVE when
    the row is outbound, thread is DRAFTED, and no sent_replies row exists.
    """
    import uuid

    from app.models.schemas.email import ThreadStateEnum
    from app.repositories.message_repo import MessageSchema
    from app.repositories.thread_repo import ThreadSchema

    mailbox = "info@sample-services.example.com"
    message_id = "AAMkAG-brittany-sent"
    conversation_id = "conv-brittany"
    thread_id = uuid.uuid4()
    msg_pk = uuid.uuid4()

    redis = AsyncMock()
    redis.get = AsyncMock(return_value="completed")
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock()

    existing = MessageSchema(
        id=msg_pk,
        thread_id=thread_id,
        graph_message_id=message_id,
        direction="outbound",
        sender=mailbox,
        body_text="I am sorry for the delayed response",
        body_preview="I am sorry for the delayed response",
        body_content_type="text",
        body_clean="I am sorry for the delayed response",
        body_clean_version=1,
        received_at=datetime(2026, 8, 26, 18, 5, tzinfo=UTC),
    )
    thread = ThreadSchema(
        id=thread_id,
        mailbox=mailbox,
        conversation_id=conversation_id,
        subject="[SampleHelpdesk] Re: Fw: Applicant Brittany Edwards",
        state=ThreadStateEnum.DRAFTED.value,
        last_message_at=existing.received_at,
        last_updated_at=existing.received_at,
    )

    with (
        patch(
            "app.services.ingestion_service.message_repo.get_by_graph_id",
            AsyncMock(return_value=existing),
        ),
        patch(
            "app.services.ingestion_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.repositories.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.sent_reply_service.resolve_thread_from_outbound",
            AsyncMock(return_value=object()),
        ) as resolve,
    ):
        result = await ingestion_service.handle_outbound_notification(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox=mailbox,
            message_id=message_id,
        )

    assert result.status == "outbound"
    assert result.thread_id == str(thread_id)
    assert resolve.await_count == 1
    assert resolve.await_args.kwargs["message"].id == msg_pk
    # No Graph re-fetch required when the row already has what resolve needs.
    graph_client.get_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbound_completed_dedup_stays_duplicate_when_already_resolved() -> None:
    import uuid

    from app.models.schemas.email import ThreadStateEnum
    from app.repositories.message_repo import MessageSchema
    from app.repositories.sent_reply_repo import SentReplySchema
    from app.repositories.thread_repo import ThreadSchema

    mailbox = "info@sample-services.example.com"
    message_id = "AAMkAG-already-done"
    thread_id = uuid.uuid4()
    msg_pk = uuid.uuid4()
    now = datetime(2026, 8, 26, 18, 5, tzinfo=UTC)

    redis = AsyncMock()
    redis.get = AsyncMock(return_value="completed")
    session = AsyncMock()
    graph_client = MagicMock()

    existing = MessageSchema(
        id=msg_pk,
        thread_id=thread_id,
        graph_message_id=message_id,
        direction="outbound",
        sender=mailbox,
        body_text="Thanks",
        body_preview="Thanks",
        body_content_type="text",
        received_at=now,
    )
    thread = ThreadSchema(
        id=thread_id,
        mailbox=mailbox,
        conversation_id="conv-done",
        subject="Re: done",
        state=ThreadStateEnum.RESOLVED.value,
        last_message_at=now,
        last_updated_at=now,
    )
    sent = SentReplySchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        message_id=msg_pk,
        draft_id=None,
        sent_body_snapshot="Thanks",
        sent_at=now,
        matched_by="time_window",
        created_at=now,
    )

    with (
        patch(
            "app.services.ingestion_service.message_repo.get_by_graph_id",
            AsyncMock(return_value=existing),
        ),
        patch(
            "app.services.ingestion_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.repositories.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=sent),
        ),
        patch(
            "app.services.sent_reply_service.resolve_thread_from_outbound",
            AsyncMock(return_value=object()),
        ) as resolve,
    ):
        result = await ingestion_service.handle_outbound_notification(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox=mailbox,
            message_id=message_id,
        )

    assert result.status == "duplicate"
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbound_notification_persists_and_resolves() -> None:
    mailbox = "user@example.com"
    message_id = "AAMkAG-sent-1"
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    session = AsyncMock()
    graph_message = GraphMessageSchema.model_validate(
        {
            "id": message_id,
            "subject": "Re: Hello",
            "bodyPreview": "Sent reply",
            "body": {"contentType": "text", "content": "Sent reply body"},
            "sender": {"emailAddress": {"address": mailbox}},
            "from": {"emailAddress": {"address": mailbox}},
            "receivedDateTime": "2026-07-09T14:00:00Z",
            "conversationId": "conv-1",
            "toRecipients": [{"emailAddress": {"address": "client@example.com"}}],
        }
    )
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(return_value=graph_message)

    thread = MagicMock()
    thread.id = __import__("uuid").uuid4()
    persisted = MagicMock()
    persisted.id = __import__("uuid").uuid4()
    persisted.body_text = "Sent reply body"
    persisted.received_at = datetime(2026, 7, 9, 14, 0, tzinfo=UTC)

    with (
        patch(
            "app.services.ingestion_service.get_settings",
            return_value=Settings(
                environment="local",
                target_mailboxes=mailbox,
            ),
        ),
        patch(
            "app.services.ingestion_service.thread_repo.find_thread_by_conversation_id",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ingestion_service.thread_repo.upsert_thread",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.ingestion_service.message_repo.create_message",
            AsyncMock(return_value=persisted),
        ) as create_msg,
        patch(
            "app.services.sent_reply_service.resolve_thread_from_outbound",
            AsyncMock(return_value=object()),
        ) as resolve,
        patch(
            "app.services.ingestion_service.complete_ingest_dedup",
            AsyncMock(),
        ),
    ):
        result = await ingestion_service.handle_outbound_notification(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox=mailbox,
            message_id=message_id,
        )

    assert result.status == "outbound"
    assert create_msg.await_args.kwargs["direction"] == "outbound"
    resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_notification_routes_sentitems_to_outbound() -> None:
    settings = _settings()
    session = AsyncMock()
    redis = AsyncMock()
    graph_client = MagicMock()
    item = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-sent",
            "clientState": "secret",
            "changeType": "created",
            "resource": "Users/user@example.com/mailFolders('SentItems')/messages/AAMkAG1",
            "resourceData": {"id": "AAMkAG1"},
        }
    )

    with patch(
        "app.services.ingestion_service.handle_outbound_notification",
        AsyncMock(return_value=IngestResultSchema(message_id="AAMkAG1", status="outbound")),
    ) as outbound:
        result = await ingestion_service.ingest_notification(
            session=session,
            redis=redis,
            graph_client=graph_client,
            notification=item,
            settings=settings,
        )

    assert result.status == "outbound"
    outbound.assert_awaited_once()


def test_is_sent_items_resource_case_insensitive() -> None:
    assert ingestion_service.is_sent_items_resource(
        "users/a@b.com/mailFolders('sentitems')/messages/xyz"
    )
    assert ingestion_service.is_sent_items_resource(
        "Users/a@b.com/mailFolders('SentItems')/Messages/xyz"
    )
    assert not ingestion_service.is_sent_items_resource(
        "users/a@b.com/mailFolders('inbox')/messages/xyz"
    )


def test_extract_folder_from_resource() -> None:
    assert (
        subscription_service.extract_folder_from_resource(
            "users/a@b.com/mailFolders('sentitems')/messages"
        )
        == "sentitems"
    )
    assert (
        subscription_service.extract_folder_from_resource(
            "users/a@b.com/mailFolders('inbox')/messages"
        )
        == "inbox"
    )
