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
from app.core.redis_keys import dedup_key
from app.models.schemas.email import EmailDirectionEnum
from app.models.schemas.graph import (
    GraphMessageSchema,
    GraphNotificationItemSchema,
    SimulateIngestRequestSchema,
)
from app.repositories.thread_repo import ThreadSchema
from app.services.ingestion_service import (
    extract_mailbox_from_resource,
    extract_message_id_from_resource,
    ingest_graph_message,
    ingest_simulated_message,
)


def _webhook_redis(**overrides: object) -> AsyncMock:
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=1)
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.get = AsyncMock(return_value=None)
    for key, value in overrides.items():
        setattr(redis, key, value)
    return redis


@pytest.fixture
def sample_message() -> GraphMessageSchema:
    return GraphMessageSchema.model_validate(
        {
            "id": "AAMkAGMessageId",
            "subject": "Invoice received",
            "bodyPreview": "Please find attached",
            "body": {"contentType": "text", "content": "Please find attached"},
            "from": {"emailAddress": {"name": "Vendor", "address": "vendor@example.com"}},
            "receivedDateTime": "2026-07-09T15:00:00Z",
            "conversationId": "AAQkAGConversationId",
        }
    )


@pytest.mark.asyncio
async def test_thread_context_from_db_includes_recipients() -> None:
    """retry_triage rebuild must restore To/CC/BCC for Haiku PoI rules and UI."""
    from app.repositories.message_repo import MessageSchema
    from app.services.ingestion_service import build_thread_context_from_db

    thread_id = uuid.uuid4()
    message_id = "msg-1"
    session = AsyncMock()
    msg_row = MessageSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id=message_id,
        direction="inbound",
        sender="vendor@example.com",
        body_text="Body",
        body_preview="Body",
        body_content_type="text",
        body_clean="Body",
        body_clean_version=1,
        received_at=datetime.now(UTC),
        to_recipients=["user@example.com"],
        cc_recipients=["cc@example.com"],
        bcc_recipients=["bcc@example.com"],
    )
    thread = ThreadSchema(
        id=thread_id,
        mailbox="user@example.com",
        conversation_id="conv-1",
        subject="Hello",
        state="NEW",
        last_message_at=datetime.now(UTC),
        last_updated_at=datetime.now(UTC),
    )

    with (
        patch(
            "app.services.ingestion_service.message_repo.get_by_graph_id",
            AsyncMock(return_value=msg_row),
        ),
        patch(
            "app.services.ingestion_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.ingestion_service.message_repo.list_by_thread",
            AsyncMock(return_value=[msg_row]),
        ),
    ):
        result = await build_thread_context_from_db(
            session, mailbox="user@example.com", message_id=message_id
        )

    assert result is not None
    assert result.status == "retry_triage"
    assert result.thread_context is not None
    rebuilt = result.thread_context.messages[0]
    assert rebuilt.to_recipients == ["user@example.com"]
    assert rebuilt.cc_recipients == ["cc@example.com"]
    assert rebuilt.bcc_recipients == ["bcc@example.com"]
    assert rebuilt.body_clean == "Body"


def test_extract_message_id_from_resource() -> None:
    resource = "users/user@example.com/messages/AAMkAGmsg123"
    assert extract_message_id_from_resource(resource) == "AAMkAGmsg123"


def test_extract_mailbox_from_resource() -> None:
    resource = "users/user@example.com/messages/AAMkAGmsg123"
    assert extract_mailbox_from_resource(resource) == "user@example.com"


def test_extract_mailbox_from_resource_strips_aad_upn_prefix() -> None:
    """Graph may echo AAD-UPN: on GUID-shaped UPNs; match TARGET_MAILBOXES without it.

    https://learn.microsoft.com/en-us/graph/outlook-change-notifications-overview
    """
    mailbox = "3f8c2a71-6d45-4e9b-a237-81c5f0d762ae@contoso.com"
    resource = f"users/AAD-UPN:{mailbox}/mailFolders('inbox')/messages"
    assert extract_mailbox_from_resource(resource) == mailbox


def test_dedup_key_prefix() -> None:
    assert dedup_key("a@b.com", "msg-1") == "dedup:a@b.com:msg-1"


def test_redis_key_helpers() -> None:
    from app.core.redis_keys import (
        MSAL_TOKEN_CACHE_KEY,
        inbox_subscription_key,
        legacy_subscription_key,
        poll_cursor_key,
        sent_items_subscription_key,
        subscription_key,
    )

    assert subscription_key("a@b.com") == "graph:sub:inbox:a@b.com"
    assert inbox_subscription_key("a@b.com") == "graph:sub:inbox:a@b.com"
    assert sent_items_subscription_key("a@b.com") == "graph:sub:sentitems:a@b.com"
    assert subscription_key("a@b.com", "sentitems") == "graph:sub:sentitems:a@b.com"
    assert legacy_subscription_key("a@b.com") == "graph:sub:a@b.com"
    assert poll_cursor_key("a@b.com") == "graph:poll:last_checked:a@b.com"
    assert poll_cursor_key("a@b.com", "inbox") == "graph:poll:last_checked:a@b.com"
    assert poll_cursor_key("a@b.com", "sentitems") == "graph:poll:last_checked:sentitems:a@b.com"
    assert MSAL_TOKEN_CACHE_KEY == "msal:token_cache"


@pytest.mark.asyncio
async def test_ingest_duplicate_returns_early(
    sample_message: GraphMessageSchema,
) -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="completed")
    redis.set = AsyncMock(return_value=True)
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
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_persists_thread_and_sibling_messages(
    sample_message: GraphMessageSchema,
) -> None:
    outbound = GraphMessageSchema.model_validate(
        {
            "id": "AAMkAGOutbound",
            "subject": "Re: Invoice received",
            "bodyPreview": "Thanks",
            "body": {"contentType": "text", "content": "Thanks"},
            "from": {"emailAddress": {"name": "Me", "address": "user@example.com"}},
            "receivedDateTime": "2026-07-09T14:00:00Z",
            "conversationId": "AAQkAGConversationId",
        }
    )

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(return_value=sample_message)
    graph_client.list_thread_messages = AsyncMock(return_value=[outbound, sample_message])

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
    assert result.thread_context is not None
    assert len(result.thread_context.messages) == 2
    assert result.thread_context.messages[0].direction == EmailDirectionEnum.OUTBOUND
    assert result.thread_context.messages[1].direction == EmailDirectionEnum.INBOUND
    upsert.assert_awaited_once()
    assert upsert.await_args.kwargs["mailbox"] == "user@example.com"
    assert create_msg.await_count == 2
    graph_client.list_thread_messages.assert_awaited_once_with(
        "user@example.com",
        "AAQkAGConversationId",
    )
    redis.set.assert_awaited_once_with(
        "dedup:user@example.com:AAMkAGMessageId",
        "processing",
        nx=True,
        ex=900,
    )


@pytest.mark.asyncio
async def test_ingest_missing_conversation_id_releases_dedup() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.eval = AsyncMock(return_value=1)
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(
        return_value=GraphMessageSchema.model_validate({"id": "msg-1", "subject": "No thread"})
    )

    with pytest.raises(GraphClientError, match="conversationId"):
        await ingest_graph_message(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox="user@example.com",
            message_id="msg-1",
        )

    redis.get.assert_awaited()
    # compare_delete only removes when value is still "processing"
    redis.eval.assert_awaited_once()
    assert redis.eval.await_args.args[2] == "dedup:user@example.com:msg-1"
    assert redis.eval.await_args.args[3] == "processing"


@pytest.mark.asyncio
async def test_ingest_includes_trigger_when_thread_list_omits_it(
    sample_message: GraphMessageSchema,
) -> None:
    """Graph list can lag get_message — trigger must still be in thread_context."""
    older = GraphMessageSchema.model_validate(
        {
            "id": "older-msg",
            "subject": "Invoice received",
            "bodyPreview": "Earlier",
            "body": {"contentType": "text", "content": "Earlier"},
            "from": {"emailAddress": {"name": "Vendor", "address": "vendor@example.com"}},
            "receivedDateTime": "2026-07-08T15:00:00Z",
            "conversationId": "AAQkAGConversationId",
        }
    )
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(return_value=sample_message)
    # Lag: list returns older messages only, not the notified trigger.
    graph_client.list_thread_messages = AsyncMock(return_value=[older])

    thread = ThreadSchema(
        id=uuid.uuid4(),
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
        ),
        patch(
            "app.services.ingestion_service.message_repo.create_message",
            AsyncMock(return_value=MagicMock(graph_message_id="AAMkAGMessageId")),
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
    assert result.thread_context is not None
    ids = {m.message_id for m in result.thread_context.messages}
    assert ids == {"older-msg", "AAMkAGMessageId"}
    assert create_msg.await_count == 2


@pytest.mark.asyncio
async def test_ingest_falls_back_to_single_message_when_thread_empty(
    sample_message: GraphMessageSchema,
) -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(return_value=sample_message)
    graph_client.list_thread_messages = AsyncMock(return_value=[])

    thread = ThreadSchema(
        id=uuid.uuid4(),
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
        ),
        patch(
            "app.services.ingestion_service.message_repo.create_message",
            AsyncMock(return_value=MagicMock(graph_message_id="AAMkAGMessageId")),
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
    assert result.thread_context is not None
    assert len(result.thread_context.messages) == 1
    create_msg.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_notification_extracts_ids_and_ingests(
    sample_message: GraphMessageSchema,
) -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(return_value=sample_message)
    graph_client.list_thread_messages = AsyncMock(return_value=[sample_message])
    notification = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-1",
            "clientState": "secret",
            "changeType": "created",
            "resource": "users/user@example.com/messages/AAMkAGMessageId",
            "resourceData": {"id": "AAMkAGMessageId"},
        }
    )
    thread = ThreadSchema(
        id=uuid.uuid4(),
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
        ),
        patch(
            "app.services.ingestion_service.message_repo.create_message",
            AsyncMock(return_value=MagicMock(graph_message_id="AAMkAGMessageId")),
        ),
    ):
        from app.services.ingestion_service import ingest_notification

        result = await ingest_notification(
            session=session,
            redis=redis,
            graph_client=graph_client,
            notification=notification,
            settings=Settings(
                environment="local",
                target_mailboxes="user@example.com",
            ),
        )

    assert result.status == "ingested"
    graph_client.get_message.assert_awaited_once_with(
        "user@example.com",
        "AAMkAGMessageId",
    )


@pytest.mark.asyncio
async def test_ingest_notification_skips_mailbox_outside_allowlist() -> None:
    from app.services.ingestion_service import ingest_notification

    notification = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-1",
            "clientState": "secret",
            "changeType": "created",
            "resource": "users/other@tenant.com/messages/msg-1",
            "resourceData": {"id": "msg-1"},
        }
    )
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock()

    result = await ingest_notification(
        session=AsyncMock(),
        redis=AsyncMock(),
        graph_client=graph_client,
        notification=notification,
        settings=Settings(
            environment="local",
            target_mailboxes="user@example.com",
        ),
    )

    assert result.status == "skipped"
    graph_client.get_message.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_notification_skips_when_mailbox_missing_from_resource() -> None:
    from app.services.ingestion_service import ingest_notification

    notification = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-1",
            "clientState": "secret",
            "changeType": "created",
            "resource": "messages/msg-1",
            "resourceData": {"id": "msg-1"},
        }
    )

    result = await ingest_notification(
        session=AsyncMock(),
        redis=AsyncMock(),
        graph_client=MagicMock(),
        notification=notification,
        settings=Settings(environment="local", target_mailboxes="user@example.com"),
    )

    assert result.status == "skipped"


@pytest.mark.asyncio
async def test_ingest_notification_raises_without_message_id() -> None:
    from app.services.ingestion_service import ingest_notification

    notification = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-1",
            "clientState": "secret",
            "changeType": "created",
            "resource": "users/user@example.com/mailFolders('inbox')/messages",
        }
    )
    with pytest.raises(GraphClientError, match="message id"):
        await ingest_notification(
            session=AsyncMock(),
            redis=AsyncMock(),
            graph_client=MagicMock(),
            notification=notification,
            settings=Settings(environment="local", target_mailboxes="user@example.com"),
        )


@pytest.mark.asyncio
async def test_webhook_empty_payload_returns_202() -> None:
    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: _webhook_redis()
    app.dependency_overrides[get_graph_client] = lambda: MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/graph/notifications",
            json={"value": []},
        )

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_webhook_invalid_payload_returns_202() -> None:
    """Poison-pill payloads must be accepted so Graph does not retry for hours."""
    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: _webhook_redis()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/graph/notifications",
            json={"value": "not-a-list"},
        )

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_webhook_enqueue_passes_only_payload_and_settings() -> None:
    """Regression: request-scoped redis/graph_client must not be passed to BackgroundTasks."""
    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )
    app.dependency_overrides[get_settings] = lambda: settings

    with patch(
        "app.api.webhooks.graph._process_notifications",
        new_callable=AsyncMock,
    ) as process:
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
    process.assert_awaited_once()
    assert len(process.await_args.args) == 2
    assert process.await_args.kwargs == {}
    assert process.await_args.args[1] is settings


@pytest.mark.asyncio
async def test_lifecycle_invalid_payload_returns_202() -> None:
    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: _webhook_redis()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/graph/lifecycle",
            json={"value": {"not": "a list"}},
        )

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_process_notifications_ingests_and_completes_dedup(
    sample_message: GraphMessageSchema,
) -> None:
    from app.api.webhooks.graph import _process_notifications
    from app.models.schemas.graph import GraphNotificationSchema, IngestResultSchema

    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )
    redis = AsyncMock()
    graph_client = MagicMock()
    payload = GraphNotificationSchema.model_validate(
        {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "clientState": "secret",
                    "changeType": "created",
                    "resource": "users/user@example.com/messages/AAMkAGMessageId",
                    "resourceData": {"id": "AAMkAGMessageId"},
                }
            ]
        }
    )

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    begin = AsyncMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin)

    session_factory = MagicMock(return_value=session)

    with (
        patch("app.api.webhooks.graph.get_session_factory", return_value=session_factory),
        patch(
            "app.services.ingestion_service.ingest_notification",
            new_callable=AsyncMock,
            return_value=IngestResultSchema(
                message_id="AAMkAGMessageId",
                status="ingested",
                conversation_id="AAQkAGConversationId",
            ),
        ),
        patch(
            "app.services.ingestion_service.complete_ingest_dedup",
            new_callable=AsyncMock,
        ) as complete,
        patch(
            "app.api.webhooks.graph.pipeline_service.run_post_ingest_triage",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as triage,
    ):
        await _process_notifications(payload, settings, redis, graph_client)

    triage.assert_awaited_once()
    complete.assert_awaited_once_with(redis, "user@example.com", "AAMkAGMessageId")


@pytest.mark.asyncio
async def test_webhook_releases_dedup_when_triage_fails() -> None:
    from app.api.webhooks.graph import _process_notifications
    from app.models.schemas.graph import GraphNotificationSchema, IngestResultSchema

    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )
    redis = AsyncMock()
    graph_client = MagicMock()
    payload = GraphNotificationSchema.model_validate(
        {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "clientState": "secret",
                    "changeType": "created",
                    "resource": "users/user@example.com/messages/AAMkAGMessageId",
                    "resourceData": {"id": "AAMkAGMessageId"},
                }
            ]
        }
    )

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    begin = AsyncMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin)
    session_factory = MagicMock(return_value=session)

    with (
        patch("app.api.webhooks.graph.get_session_factory", return_value=session_factory),
        patch(
            "app.services.ingestion_service.ingest_notification",
            new_callable=AsyncMock,
            return_value=IngestResultSchema(
                message_id="AAMkAGMessageId",
                status="ingested",
                conversation_id="AAQkAGConversationId",
            ),
        ),
        patch(
            "app.services.ingestion_service.complete_ingest_dedup",
            new_callable=AsyncMock,
        ) as complete,
        patch(
            "app.services.ingestion_service.release_ingest_dedup",
            new_callable=AsyncMock,
        ) as release,
        patch(
            "app.api.webhooks.graph.pipeline_service.run_post_ingest_triage",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        await _process_notifications(payload, settings, redis, graph_client)

    complete.assert_not_awaited()
    release.assert_awaited_once_with(redis, "user@example.com", "AAMkAGMessageId")


@pytest.mark.asyncio
async def test_webhook_accepts_but_skips_forged_client_state() -> None:
    """Wrong clientState still returns 202 and must not enqueue background work."""
    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(
        environment="local",
        graph_webhook_client_state="expected-secret",
        target_mailboxes="user@example.com",
    )
    redis = AsyncMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_graph_client] = lambda: MagicMock()

    with patch(
        "app.api.webhooks.graph._process_notifications",
        new_callable=AsyncMock,
    ) as process:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/graph/notifications",
                json={
                    "value": [
                        {
                            "subscriptionId": "sub-1",
                            "clientState": "forged-secret",
                            "changeType": "created",
                            "resource": "users/user@example.com/messages/msg-1",
                            "resourceData": {"id": "msg-1"},
                        }
                    ]
                },
            )

    assert response.status_code == 202
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_notifications_skips_mismatched_client_state() -> None:
    from app.api.webhooks.graph import _process_notifications
    from app.models.schemas.graph import GraphNotificationSchema

    settings = Settings(
        environment="local",
        graph_webhook_client_state="expected-secret",
        target_mailboxes="user@example.com",
    )
    redis = AsyncMock()
    graph_client = MagicMock()
    payload = GraphNotificationSchema.model_validate(
        {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "clientState": "forged-secret",
                    "changeType": "created",
                    "resource": "users/user@example.com/messages/msg-1",
                    "resourceData": {"id": "msg-1"},
                }
            ]
        }
    )

    with (
        patch("app.api.webhooks.graph.get_session_factory", return_value=MagicMock()),
        patch(
            "app.services.ingestion_service.ingest_notification",
            new_callable=AsyncMock,
        ) as ingest,
    ):
        await _process_notifications(payload, settings, redis, graph_client)

    ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_acks_when_client_state_unconfigured() -> None:
    """Missing GRAPH_WEBHOOK_CLIENT_STATE must ACK 202 (not 502) per Graph delivery contract."""
    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(
        environment="local",
        graph_webhook_client_state="",
        target_mailboxes="user@example.com",
    )
    redis = AsyncMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_graph_client] = lambda: MagicMock()

    with patch(
        "app.api.webhooks.graph._process_notifications",
        new_callable=AsyncMock,
    ) as process:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/graph/notifications",
                json={
                    "value": [
                        {
                            "subscriptionId": "sub-1",
                            "clientState": "anything",
                            "changeType": "created",
                            "resource": "users/user@example.com/messages/msg-1",
                            "resourceData": {"id": "msg-1"},
                        }
                    ]
                },
            )

    assert response.status_code == 202
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_simulated_message_persists() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    session = AsyncMock()
    thread_id = uuid.uuid4()
    thread = ThreadSchema(
        id=thread_id,
        mailbox="user@example.com",
        conversation_id="conv-1",
        subject="Hello",
        state="NEW",
        last_message_at=datetime.now(UTC),
        last_updated_at=datetime.now(UTC),
    )
    payload = SimulateIngestRequestSchema(
        mailbox="user@example.com",
        message_id="sim-1",
        conversation_id="conv-1",
        sender="vendor@example.com",
        subject="Hello",
        body_text="Body",
        received_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
    )

    with (
        patch(
            "app.services.ingestion_service.thread_repo.upsert_thread",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.ingestion_service.message_repo.create_message",
            AsyncMock(return_value=MagicMock(graph_message_id="sim-1")),
        ),
    ):
        result = await ingest_simulated_message(
            session=session,
            redis=redis,
            payload=payload,
        )

    assert result.status == "ingested"
    assert result.thread_context is not None
    assert result.thread_context.messages[0].direction == EmailDirectionEnum.INBOUND
    redis.set.assert_awaited_once_with(
        "dedup:user@example.com:sim-1",
        "processing",
        nx=True,
        ex=900,
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
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="1")

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis
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
async def test_webhook_validation_rejected_without_pending_window() -> None:
    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_graph_client] = lambda: MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/graph/notifications",
            params={"validationToken": "test%20123"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_lifecycle_validation_handshake_returns_plain_text() -> None:
    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(environment="local", graph_webhook_client_state="secret")
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="1")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_graph_client] = lambda: MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/graph/lifecycle",
            params={"validationToken": "life%20token"},
        )

    assert response.status_code == 200
    assert response.text == "life token"


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
    app.dependency_overrides[get_redis] = lambda: _webhook_redis()
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


@pytest.mark.asyncio
async def test_lifecycle_notification_returns_202() -> None:
    app = FastAPI()
    app.include_router(graph_router)

    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: _webhook_redis()
    app.dependency_overrides[get_graph_client] = lambda: MagicMock()

    with patch(
        "app.api.webhooks.graph._process_lifecycle_notifications",
        new_callable=AsyncMock,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/graph/lifecycle",
                json={
                    "value": [
                        {
                            "subscriptionId": "sub-1",
                            "clientState": "secret",
                            "lifecycleEvent": "missed",
                            "resource": "users/user@example.com/mailFolders('inbox')/messages",
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


def test_lifecycle_item_schema_parses() -> None:
    item = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-1",
            "clientState": "secret",
            "lifecycleEvent": "reauthorizationRequired",
            "resource": "users/user@example.com/mailFolders('inbox')/messages",
        }
    )
    assert item.lifecycle_event == "reauthorizationRequired"
    assert item.change_type is None


@pytest.mark.asyncio
async def test_webhook_rejects_oversized_body_without_content_length() -> None:
    """Body cap must apply even when Content-Length is absent (chunked/missing)."""
    from app.core.dependencies import get_redis, get_settings

    app = FastAPI()
    app.include_router(graph_router)
    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        webhook_max_body_bytes=1024,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: _webhook_redis()

    huge = b"x" * 2048
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/graph/notifications",
            content=huge,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_webhook_rate_limit_still_enqueues_valid_client_state() -> None:
    """Over-limit with matching clientState must still ACK 202 and enqueue."""
    from app.core.dependencies import get_redis, get_settings

    app = FastAPI()
    app.include_router(graph_router)
    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        webhook_rate_limit_per_minute=10,
        trust_x_forwarded_for=False,
    )
    redis = _webhook_redis()
    redis.eval = AsyncMock(return_value=11)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis

    with patch(
        "app.api.webhooks.graph.enqueue",
    ) as enqueue_mock:
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
                headers={"X-Forwarded-For": "203.0.113.99"},
            )

    assert response.status_code == 202
    enqueue_mock.assert_called_once()
    # Untrusted: rate key from peer, not spoofed XFF.
    call_args = redis.eval.await_args
    assert call_args is not None
    rate_key = call_args.args[2]
    assert "203.0.113.99" not in rate_key


@pytest.mark.asyncio
async def test_webhook_rate_limit_skips_bad_client_state() -> None:
    """Over-limit + wrong clientState ACKs 202 without enqueue (abuse path)."""
    from app.core.dependencies import get_redis, get_settings

    app = FastAPI()
    app.include_router(graph_router)
    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        webhook_rate_limit_per_minute=10,
    )
    redis = _webhook_redis()
    redis.eval = AsyncMock(return_value=11)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis

    with patch(
        "app.api.webhooks.graph.enqueue",
    ) as enqueue_mock:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/graph/notifications",
                json={
                    "value": [
                        {
                            "subscriptionId": "sub-1",
                            "clientState": "wrong",
                            "changeType": "created",
                            "resource": "users/user@example.com/messages/msg-1",
                            "resourceData": {"id": "msg-1"},
                        }
                    ]
                },
            )

    assert response.status_code == 202
    enqueue_mock.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_rate_limit_uses_x_real_ip_when_trusted() -> None:
    """When TRUST_X_FORWARDED_FOR, bucket on nginx X-Real-IP."""
    from app.core.dependencies import get_redis, get_settings

    app = FastAPI()
    app.include_router(graph_router)
    settings = Settings(
        environment="local",
        graph_webhook_client_state="secret",
        webhook_rate_limit_per_minute=10,
        trust_x_forwarded_for=True,
    )
    redis = _webhook_redis()
    redis.eval = AsyncMock(return_value=1)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis

    with patch("app.api.webhooks.graph.enqueue"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
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
                headers={
                    "X-Real-IP": "198.51.100.10",
                    "X-Forwarded-For": "203.0.113.99, 198.51.100.10",
                },
            )

    call_args = redis.eval.await_args
    assert call_args is not None
    rate_key = call_args.args[2]
    assert "198.51.100.10" in rate_key
    assert "203.0.113.99" not in rate_key
