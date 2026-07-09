"""Unit tests for ingestion service and Graph webhook validation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.webhooks.graph import router as graph_router
from app.core.config import Settings
from app.core.dependencies import get_graph_client, get_redis, get_settings
from app.core.exceptions import GraphClientError
from app.models.schemas.graph import GraphMessageSchema, GraphNotificationItemSchema
from app.repositories.thread_repo import ThreadSchema
from app.services.ingestion_service import (
    extract_mailbox_from_resource,
    extract_message_id_from_resource,
    ingest_graph_message,
)


@pytest.fixture
def sample_message() -> GraphMessageSchema:
    return GraphMessageSchema.model_validate(
        {
            "id": "AAMkAGMessageId",
            "subject": "Invoice received",
            "bodyPreview": "Please find attached",
            "body": {"contentType": "text", "content": "Please find attached"},
            "from": {
                "emailAddress": {"name": "Vendor", "address": "vendor@example.com"}
            },
            "receivedDateTime": "2026-07-09T15:00:00Z",
            "conversationId": "AAQkAGConversationId",
        }
    )


def test_extract_message_id_from_resource() -> None:
    resource = "users/user@example.com/messages/AAMkAGmsg123"
    assert extract_message_id_from_resource(resource) == "AAMkAGmsg123"


def test_extract_mailbox_from_resource() -> None:
    resource = "users/user@example.com/messages/AAMkAGmsg123"
    assert extract_mailbox_from_resource(resource) == "user@example.com"


@pytest.mark.asyncio
async def test_ingest_duplicate_returns_early(
    sample_message: GraphMessageSchema,
) -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=False)
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock()

    result = await ingest_graph_message(
        session=session,
        redis=redis,
        graph_client=graph_client,
        mailbox="user@example.com",
        message_id="AAMkAGMessageId",
    )

    assert result.status == "duplicate"
    graph_client.get_message.assert_not_called()
    redis.set.assert_awaited_once_with(
        "user@example.com:AAMkAGMessageId",
        "1",
        nx=True,
        ex=86_400,
    )


@pytest.mark.asyncio
async def test_ingest_persists_thread_and_message(
    sample_message: GraphMessageSchema,
) -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(return_value=sample_message)
    graph_client.list_thread_messages = AsyncMock(return_value=[sample_message])

    thread_id = uuid.uuid4()
    thread = ThreadSchema(
        id=thread_id,
        mailbox="user@example.com",
        conversation_id="AAQkAGConversationId",
        subject="Invoice received",
        state="NEW",
        last_message_at=datetime.now(UTC),
        last_updated_at=datetime.now(UTC),
    )

    with (
        patch(
            "app.services.ingestion_service.thread_repo.upsert_thread",
            AsyncMock(return_value=thread),
        ) as upsert,
        patch(
            "app.services.ingestion_service.message_repo.create_message",
            AsyncMock(
                return_value=MagicMock(
                    graph_message_id="AAMkAGMessageId",
                )
            ),
        ) as create_msg,
    ):
        result = await ingest_graph_message(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox="user@example.com",
            message_id="AAMkAGMessageId",
        )

    assert result.status == "ingested"
    assert result.conversation_id == "AAQkAGConversationId"
    upsert.assert_awaited_once()
    create_msg.assert_awaited_once()
    graph_client.list_thread_messages.assert_awaited_once_with(
        "user@example.com",
        "AAQkAGConversationId",
    )


@pytest.mark.asyncio
async def test_ingest_missing_conversation_id_raises() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(
        return_value=GraphMessageSchema.model_validate(
            {"id": "msg-1", "subject": "No thread"}
        )
    )

    with pytest.raises(GraphClientError, match="conversationId"):
        await ingest_graph_message(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox="user@example.com",
            message_id="msg-1",
        )


@pytest.mark.asyncio
async def test_webhook_validation_handshake_returns_plain_text() -> None:
    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: AsyncMock()
    app.dependency_overrides[get_graph_client] = lambda: MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/graph/notifications",
            params={"validationToken": "test%20123"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "test 123"


@pytest.mark.asyncio
async def test_webhook_notification_returns_202() -> None:
    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: AsyncMock()
    app.dependency_overrides[get_graph_client] = lambda: MagicMock()

    with patch(
        "app.api.webhooks.graph._process_notifications",
        new_callable=AsyncMock,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/graph/notifications",
                json={
                    "value": [
                        {
                            "subscriptionId": "sub-1",
                            "clientState": "secret",
                            "changeType": "created",
                            "resource": "users/user@example.com/messages/msg-1",
                            "resourceData": {"id": "msg-1"},
                        }
                    ]
                },
            )

    assert response.status_code == 202


def test_notification_item_schema_parses_camel_case() -> None:
    item = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-1",
            "clientState": "secret",
            "changeType": "created",
            "resource": "users/user@example.com/messages/msg-1",
            "resourceData": {"id": "msg-1"},
        }
    )
    assert item.subscription_id == "sub-1"
    assert item.client_state == "secret"
    assert item.resource_data is not None
    assert item.resource_data.id == "msg-1"
