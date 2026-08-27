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


def _persisted_from_create(session, **kwargs):  # noqa: ANN001
    """Stand-in row for create_message — mirrors fields resolve needs."""
    row = MagicMock()
    row.id = uuid.uuid4()
    row.graph_message_id = kwargs["graph_message_id"]
    row.direction = kwargs["direction"]
    row.received_at = kwargs["received_at"]
    row.body_text = kwargs.get("body_text") or ""
    row.unique_body_text = kwargs.get("unique_body_text")
    row.body_preview = kwargs.get("body_preview")
    row.meeting_message_type = kwargs.get("meeting_message_type")
    return row


@pytest.mark.asyncio
async def test_shared_mailbox_tip_send_resolves_without_reviewer_from() -> None:
    """Brittany / SampleHelpdesk case: Elise replies from the shared mailbox itself.

    Conversation sync persists the tip as direction=outbound (From == mailbox),
    but From is not a REVIEWER_MAILBOXES address. Spec: tip outbound must still
    return status='outbound' so poll/webhook skip Sonnet draft and run sent-reply
    resolve — not leave the thread DRAFTED with a plain Sent badge only.
    """
    info = "info@sample-services.example.com"
    helpdesk = "helpdesk@sample-helpdesk.example.com"
    conversation_id = "conv-brittany"
    inbound_id = "msg-inbound"
    outbound_id = "msg-outbound-tip"

    inbound = GraphMessageSchema.model_validate(
        {
            "id": inbound_id,
            "subject": "[SampleHelpdesk] Re: Fw: Applicant Brittany Edwards",
            "bodyPreview": "Please advise",
            "body": {"contentType": "text", "content": "Please advise"},
            "from": {"emailAddress": {"address": helpdesk}},
            "receivedDateTime": "2026-08-26T17:00:00Z",
            "conversationId": conversation_id,
        }
    )
    outbound = GraphMessageSchema.model_validate(
        {
            "id": outbound_id,
            "subject": "[SampleHelpdesk] Re: Fw: Applicant Brittany Edwards",
            "bodyPreview": "I am sorry for the delayed response",
            "body": {
                "contentType": "text",
                "content": "I am sorry for the delayed response",
            },
            "from": {"emailAddress": {"address": info}},
            "receivedDateTime": "2026-08-26T18:05:00Z",
            "conversationId": conversation_id,
        }
    )

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(return_value=outbound)
    graph_client.list_thread_messages = AsyncMock(return_value=[inbound, outbound])

    thread = _thread(mailbox=info, conversation_id=conversation_id)
    settings = Settings(
        environment="local",
        target_mailboxes=info,
        reviewer_mailboxes="elise@sample-site.example.com",
    )

    with (
        patch("app.services.ingestion_service.get_settings", return_value=settings),
        patch(
            "app.services.ingestion_service.thread_repo.upsert_thread",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.ingestion_service.message_repo.create_message",
            AsyncMock(side_effect=_persisted_from_create),
        ),
        patch(
            "app.services.sent_reply_service.resolve_thread_from_outbound",
            AsyncMock(return_value=object()),
        ) as resolve,
        patch("app.services.ingestion_service.complete_ingest_dedup", AsyncMock()),
        patch(
            "app.services.ingestion_service._maybe_set_alert_fingerprint",
            AsyncMock(),
        ),
    ):
        result = await ingest_graph_message(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox=info,
            message_id=outbound_id,
        )

    # Public seam: outbound status keeps this off the Sonnet draft path.
    assert result.status == "outbound"
    assert result.thread_id == str(thread.id)
    assert resolve.await_count == 1
    assert resolve.await_args.kwargs["message"].graph_message_id == outbound_id


@pytest.mark.asyncio
async def test_older_shared_send_does_not_resolve_when_tip_is_inbound() -> None:
    """Customer reply after our send: tip is inbound → stay on triage path."""
    info = "info@sample-services.example.com"
    helpdesk = "helpdesk@sample-helpdesk.example.com"
    conversation_id = "conv-followup"
    outbound_id = "msg-our-old-send"
    inbound_id = "msg-customer-new"

    outbound = GraphMessageSchema.model_validate(
        {
            "id": outbound_id,
            "subject": "Re: Applicant",
            "bodyPreview": "Here is the packet",
            "body": {"contentType": "text", "content": "Here is the packet"},
            "from": {"emailAddress": {"address": info}},
            "receivedDateTime": "2026-08-26T12:00:00Z",
            "conversationId": conversation_id,
        }
    )
    inbound = GraphMessageSchema.model_validate(
        {
            "id": inbound_id,
            "subject": "Re: Applicant",
            "bodyPreview": "Thanks — one more question",
            "body": {"contentType": "text", "content": "Thanks — one more question"},
            "from": {"emailAddress": {"address": helpdesk}},
            "receivedDateTime": "2026-08-26T19:00:00Z",
            "conversationId": conversation_id,
        }
    )

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    session = AsyncMock()
    graph_client = MagicMock()
    graph_client.get_message = AsyncMock(return_value=inbound)
    graph_client.list_thread_messages = AsyncMock(return_value=[outbound, inbound])

    thread = _thread(mailbox=info, conversation_id=conversation_id)
    settings = Settings(
        environment="local",
        target_mailboxes=info,
        reviewer_mailboxes="elise@sample-site.example.com",
    )

    with (
        patch("app.services.ingestion_service.get_settings", return_value=settings),
        patch(
            "app.services.ingestion_service.thread_repo.upsert_thread",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.ingestion_service.message_repo.create_message",
            AsyncMock(side_effect=_persisted_from_create),
        ),
        patch(
            "app.services.sent_reply_service.resolve_thread_from_outbound",
            AsyncMock(return_value=object()),
        ) as resolve,
        patch(
            "app.services.ingestion_service._maybe_set_alert_fingerprint",
            AsyncMock(),
        ),
    ):
        result = await ingest_graph_message(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox=info,
            message_id=inbound_id,
        )

    assert result.status == "ingested"
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_triage_resolves_when_db_tip_is_outbound() -> None:
    """Stuck processing + tip already our send must not Sonnet-draft again."""
    from app.repositories.message_repo import MessageSchema
    from app.services.ingestion_service import build_thread_context_from_db

    info = "info@sample-services.example.com"
    thread_id = uuid.uuid4()
    tip_graph_id = "msg-tip-sent"
    older = MessageSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id="msg-older-inbound",
        direction="inbound",
        sender="helpdesk@sample-helpdesk.example.com",
        body_text="Please advise",
        body_preview="Please advise",
        body_content_type="text",
        body_clean="Please advise",
        body_clean_version=1,
        received_at=datetime(2026, 8, 26, 17, 0, tzinfo=UTC),
    )
    tip = MessageSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id=tip_graph_id,
        direction="outbound",
        sender=info,
        body_text="I am sorry for the delayed response",
        body_preview="I am sorry for the delayed response",
        body_content_type="text",
        body_clean="I am sorry for the delayed response",
        body_clean_version=1,
        received_at=datetime(2026, 8, 26, 18, 5, tzinfo=UTC),
    )
    thread = ThreadSchema(
        id=thread_id,
        mailbox=info,
        conversation_id="conv-retry-tip",
        subject="Re: Brittany",
        state="DRAFTED",
        last_message_at=tip.received_at,
        last_updated_at=tip.received_at,
    )
    session = AsyncMock()

    with (
        patch(
            "app.services.ingestion_service.message_repo.get_by_graph_id",
            AsyncMock(return_value=tip),
        ),
        patch(
            "app.services.ingestion_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.ingestion_service.message_repo.list_by_thread",
            AsyncMock(return_value=[older, tip]),
        ),
        patch(
            "app.services.sent_reply_service.resolve_thread_from_outbound",
            AsyncMock(return_value=object()),
        ) as resolve,
    ):
        result = await build_thread_context_from_db(
            session,
            mailbox=info,
            message_id=tip_graph_id,
        )

    assert result is not None
    assert result.status == "outbound"
    assert resolve.await_count == 1
    assert resolve.await_args.kwargs["message"].graph_message_id == tip_graph_id
