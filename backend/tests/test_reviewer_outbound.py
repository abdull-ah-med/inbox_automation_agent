"""Reviewer replies sent from a personal mailbox must attach to the shared thread."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.redis_keys import DEDUP_TTL_SECONDS, DEDUP_VALUE_COMPLETED, dedup_key
from app.models.schemas.email import EmailDirectionEnum
from app.models.schemas.graph import GraphMessageSchema
from app.repositories.thread_repo import ThreadSchema
from app.services.ingestion_service import (
    _direction_for_sender,
    handle_outbound_notification,
    ingest_graph_message,
)


def test_reviewer_list_is_separate_from_target_mailboxes() -> None:
    settings = Settings(
        environment="local",
        target_mailboxes="inquiries@sample-site.example.com",
        reviewer_mailboxes="elise@sample-site.example.com",
    )
    assert settings.mailbox_list == ["inquiries@sample-site.example.com"]
    assert settings.reviewer_mailbox_list == ["elise@sample-site.example.com"]
    assert settings.is_reviewer_address("Elise@sample-site.example.com")
    assert not settings.is_reviewer_address("inquiries@sample-site.example.com")
    assert not settings.mailbox_allowed("elise@sample-site.example.com")
    assert settings.outbound_mailbox_allowed("elise@sample-site.example.com")


def test_inbound_copy_from_reviewer_is_treated_as_outbound() -> None:
    settings = Settings(
        environment="local",
        reviewer_mailboxes="elise@sample-site.example.com",
    )
    assert (
        _direction_for_sender(
            "inquiries@sample-site.example.com",
            "elise@sample-site.example.com",
            settings=settings,
        )
        == EmailDirectionEnum.OUTBOUND
    )
    assert (
        _direction_for_sender(
            "inquiries@sample-site.example.com",
            "client@vendor.example",
            settings=settings,
        )
        == EmailDirectionEnum.INBOUND
    )


def _thread(*, mailbox: str, conversation_id: str) -> ThreadSchema:
    now = datetime.now(UTC)
    return ThreadSchema(
        id=uuid.uuid4(),
        mailbox=mailbox,
        conversation_id=conversation_id,
        subject="Background check",
        state="DRAFTED",
        last_message_at=now,
        last_updated_at=now,
    )


@pytest.mark.asyncio
async def test_personal_sent_item_attaches_to_shared_inbox_thread() -> None:
    """Sent Items in Elise's mailbox must resolve the inquiries thread, not a new one."""
    inquiries = "inquiries@sample-site.example.com"
    elise = "elise@sample-site.example.com"
    conversation_id = "conv-shared"
    message_id = "AAMkAG-elise-sent"
    shared_thread = _thread(mailbox=inquiries, conversation_id=conversation_id)

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    session = AsyncMock()
    graph_message = GraphMessageSchema.model_validate(
        {
            "id": message_id,
            "subject": "Re: Background check",
            "bodyPreview": "Sent from my mailbox",
            "body": {"contentType": "text", "content": "Sent from my mailbox"},
            "sender": {"emailAddress": {"address": elise}},
            "from": {"emailAddress": {"address": elise}},
            "receivedDateTime": "2026-08-19T14:00:00Z",
            "conversationId": conversation_id,
            "toRecipients": [{"emailAddress": {"address": "client@vendor.example"}}],
        }
    )
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(return_value=graph_message)
    persisted = MagicMock()
    persisted.id = uuid.uuid4()
    persisted.body_text = "Sent from my mailbox"
    persisted.received_at = datetime(2026, 8, 19, 14, 0, tzinfo=UTC)

    settings = Settings(
        environment="local",
        target_mailboxes=inquiries,
        reviewer_mailboxes=elise,
    )

    with (
        patch("app.services.ingestion_service.get_settings", return_value=settings),
        patch(
            "app.services.ingestion_service.thread_repo.find_thread_by_conversation_id",
            AsyncMock(return_value=shared_thread),
        ) as find_thread,
        patch(
            "app.services.ingestion_service.thread_repo.upsert_thread",
            AsyncMock(return_value=shared_thread),
        ) as upsert,
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
        result = await handle_outbound_notification(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox=elise,
            message_id=message_id,
        )

    assert result.status == "outbound"
    assert result.thread_id == str(shared_thread.id)
    find_thread.assert_awaited_once()
    assert upsert.await_args.kwargs["mailbox"] == inquiries
    assert create_msg.await_args.kwargs["thread_id"] == shared_thread.id
    assert create_msg.await_args.kwargs["direction"] == "outbound"
    assert resolve.await_args.kwargs["thread_id"] == shared_thread.id
    assert resolve.await_args.kwargs["mailbox"] == inquiries


@pytest.mark.asyncio
async def test_reviewer_copy_landing_in_target_inbox_completes_dedup() -> None:
    """B7: a reviewer's own copy of a reply, ingested via the *inbound* poll of
    the target mailbox, resolves the thread and returns status='outbound' —
    but never advances through `_TRIAGE_ELIGIBLE`, so no caller (poller or
    webhook) ever calls complete_ingest_dedup for it downstream. Left at
    'processing' until DEDUP_PROCESSING_TTL_SECONDS elapses, the next poll
    window re-claims and re-resolves the same message repeatedly.

    Independent oracle: read the actual Redis key the branch is supposed to
    have written, not just whether some mock was awaited.
    """
    inquiries = "inquiries@sample-site.example.com"
    elise = "elise@sample-site.example.com"
    conversation_id = "conv-reviewer-copy"
    message_id = "AAMkAG-reviewer-copy"

    store: dict[str, tuple[str, int | None]] = {}

    async def fake_get(key: str) -> str | None:
        entry = store.get(key)
        return entry[0] if entry else None

    async def fake_set(key: str, value: str, *, nx: bool = False, ex: int | None = None):
        if nx and key in store:
            return None
        store[key] = (value, ex)
        return True

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=fake_get)
    redis.set = AsyncMock(side_effect=fake_set)

    session = AsyncMock()
    graph_message = GraphMessageSchema.model_validate(
        {
            "id": message_id,
            "subject": "Re: Background check",
            "bodyPreview": "Reviewer's own copy",
            "body": {"contentType": "text", "content": "Reviewer's own copy"},
            "sender": {"emailAddress": {"address": elise}},
            "from": {"emailAddress": {"address": elise}},
            "receivedDateTime": "2026-08-19T14:00:00Z",
            "conversationId": conversation_id,
            "toRecipients": [{"emailAddress": {"address": "client@vendor.example"}}],
        }
    )
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(return_value=graph_message)
    graph_client.list_thread_messages = AsyncMock(return_value=[graph_message])

    thread = _thread(mailbox=inquiries, conversation_id=conversation_id)
    persisted = MagicMock()
    persisted.id = uuid.uuid4()
    persisted.graph_message_id = message_id

    settings = Settings(
        environment="local",
        target_mailboxes=inquiries,
        reviewer_mailboxes=elise,
    )

    with (
        patch("app.services.ingestion_service.get_settings", return_value=settings),
        patch(
            "app.services.ingestion_service.thread_repo.upsert_thread",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.ingestion_service.message_repo.create_message",
            AsyncMock(return_value=persisted),
        ),
        patch(
            "app.services.sent_reply_service.resolve_thread_from_outbound",
            AsyncMock(return_value=object()),
        ),
    ):
        result = await ingest_graph_message(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox=inquiries,
            message_id=message_id,
        )

    assert result.status == "outbound"

    key = dedup_key(inquiries, message_id)
    assert key in store, "dedup key must be resolved, not left dangling"
    value, ttl = store[key]
    assert value == DEDUP_VALUE_COMPLETED
    assert ttl == DEDUP_TTL_SECONDS
