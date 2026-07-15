"""Unit tests for poll fallback worker."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.redis_keys import poll_cursor_key
from app.models.schemas.graph import GraphMessageSchema, IngestResultSchema
from app.workers.poll_fallback_worker import poll_mailbox, run_poll_all_mailboxes


@pytest.mark.asyncio
async def test_poll_mailbox_ingests_and_updates_cursor() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)

    msg = GraphMessageSchema.model_validate(
        {
            "id": "msg-1",
            "subject": "Hello",
            "receivedDateTime": "2026-07-09T12:00:00Z",
            "conversationId": "conv-1",
            "from": {"emailAddress": {"address": "a@b.com"}},
        }
    )
    graph_client = MagicMock()
    graph_client.list_messages = AsyncMock(return_value=[msg])

    session = MagicMock()

    class _CM:
        async def __aenter__(self) -> MagicMock:
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    session.begin = MagicMock(return_value=_CM())
    factory_cm = _CM()

    with (
        patch(
            "app.workers.poll_fallback_worker.get_session_factory",
            return_value=lambda: factory_cm,
        ),
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.ingest_graph_message",
            AsyncMock(return_value=IngestResultSchema(message_id="msg-1", status="ingested")),
        ) as ingest,
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.complete_ingest_dedup",
            AsyncMock(),
        ) as complete,
    ):
        await poll_mailbox(
            "user@example.com",
            redis=redis,
            graph_client=graph_client,
        )

    graph_client.list_messages.assert_awaited_once()
    kwargs = graph_client.list_messages.await_args.kwargs
    assert "receivedDateTime ge " in kwargs["filter_query"]
    assert kwargs["follow_next_link"] is True
    ingest.assert_awaited_once()
    complete.assert_awaited_once_with(redis, "user@example.com", "msg-1")
    redis.set.assert_awaited()
    assert redis.set.await_args.args[0] == poll_cursor_key("user@example.com")
    assert redis.set.await_args.args[1] == "2026-07-09T12:00:01Z"


@pytest.mark.asyncio
async def test_poll_mailbox_does_not_advance_cursor_past_failure() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="2026-07-09T10:00:00Z")
    redis.set = AsyncMock(return_value=True)

    msg_ok = GraphMessageSchema.model_validate(
        {
            "id": "msg-1",
            "subject": "Ok",
            "receivedDateTime": "2026-07-09T11:00:00Z",
            "conversationId": "conv-1",
        }
    )
    msg_fail = GraphMessageSchema.model_validate(
        {
            "id": "msg-2",
            "subject": "Fail",
            "receivedDateTime": "2026-07-09T12:00:00Z",
            "conversationId": "conv-2",
        }
    )
    msg_later = GraphMessageSchema.model_validate(
        {
            "id": "msg-3",
            "subject": "Later",
            "receivedDateTime": "2026-07-09T13:00:00Z",
            "conversationId": "conv-3",
        }
    )
    graph_client = MagicMock()
    graph_client.list_messages = AsyncMock(return_value=[msg_ok, msg_fail, msg_later])

    class _CM:
        async def __aenter__(self) -> MagicMock:
            return MagicMock()

        async def __aexit__(self, *args: object) -> None:
            return None

    async def _ingest(**kwargs: object) -> IngestResultSchema:
        message_id = kwargs["message_id"]
        if message_id == "msg-2":
            raise RuntimeError("graph down")
        return IngestResultSchema(message_id=str(message_id), status="ingested")

    with (
        patch(
            "app.workers.poll_fallback_worker.get_session_factory",
            return_value=lambda: _CM(),
        ),
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.ingest_graph_message",
            AsyncMock(side_effect=_ingest),
        ),
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.complete_ingest_dedup",
            AsyncMock(),
        ),
    ):
        await poll_mailbox(
            "user@example.com",
            redis=redis,
            graph_client=graph_client,
        )

    # Cursor stops at last contiguous success (msg-1), not msg-3.
    assert redis.set.await_args.args[0] == poll_cursor_key("user@example.com")
    assert redis.set.await_args.args[1] == "2026-07-09T11:00:00Z"


@pytest.mark.asyncio
async def test_poll_mailbox_uses_existing_cursor() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="2026-07-09T10:00:00Z")
    redis.set = AsyncMock(return_value=True)
    graph_client = MagicMock()
    graph_client.list_messages = AsyncMock(return_value=[])

    class _CM:
        async def __aenter__(self) -> MagicMock:
            return MagicMock()

        async def __aexit__(self, *args: object) -> None:
            return None

    with patch(
        "app.workers.poll_fallback_worker.get_session_factory",
        return_value=lambda: _CM(),
    ):
        await poll_mailbox(
            "user@example.com",
            redis=redis,
            graph_client=graph_client,
        )

    filter_query = graph_client.list_messages.await_args.kwargs["filter_query"]
    assert filter_query == "receivedDateTime ge 2026-07-09T10:00:00Z"


@pytest.mark.asyncio
async def test_poll_mailbox_partial_failure_same_second_does_not_skip_retry() -> None:
    """Regression: +1s on partial failure would skip a failed same-second message."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="2026-07-09T10:00:00Z")
    redis.set = AsyncMock(return_value=True)

    msg_ok = GraphMessageSchema.model_validate(
        {
            "id": "msg-1",
            "subject": "Ok",
            "receivedDateTime": "2026-07-09T12:00:00Z",
            "conversationId": "conv-1",
        }
    )
    msg_fail = GraphMessageSchema.model_validate(
        {
            "id": "msg-2",
            "subject": "Fail",
            "receivedDateTime": "2026-07-09T12:00:00Z",
            "conversationId": "conv-2",
        }
    )
    graph_client = MagicMock()
    graph_client.list_messages = AsyncMock(return_value=[msg_ok, msg_fail])

    class _CM:
        async def __aenter__(self) -> MagicMock:
            return MagicMock()

        async def __aexit__(self, *args: object) -> None:
            return None

    async def _ingest(**kwargs: object) -> IngestResultSchema:
        if kwargs["message_id"] == "msg-2":
            raise RuntimeError("graph down")
        return IngestResultSchema(message_id="msg-1", status="ingested")

    with (
        patch(
            "app.workers.poll_fallback_worker.get_session_factory",
            return_value=lambda: _CM(),
        ),
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.ingest_graph_message",
            AsyncMock(side_effect=_ingest),
        ),
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.complete_ingest_dedup",
            AsyncMock(),
        ),
    ):
        await poll_mailbox(
            "user@example.com",
            redis=redis,
            graph_client=graph_client,
        )

    # Inclusive watermark so next poll still sees msg-2 (ge 12:00:00).
    assert redis.set.await_args.args[1] == "2026-07-09T12:00:00Z"


@pytest.mark.asyncio
async def test_poll_full_success_cursor_excludes_last_message_on_next_ge() -> None:
    """After full success, cursor is past last received second so ge won't re-fetch it."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    msg = GraphMessageSchema.model_validate(
        {
            "id": "msg-1",
            "receivedDateTime": "2026-07-09T12:00:00Z",
            "conversationId": "conv-1",
        }
    )
    graph_client = MagicMock()
    graph_client.list_messages = AsyncMock(return_value=[msg])

    class _CM:
        async def __aenter__(self) -> MagicMock:
            return MagicMock()

        async def __aexit__(self, *args: object) -> None:
            return None

    with (
        patch(
            "app.workers.poll_fallback_worker.get_session_factory",
            return_value=lambda: _CM(),
        ),
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.ingest_graph_message",
            AsyncMock(return_value=IngestResultSchema(message_id="msg-1", status="ingested")),
        ),
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.complete_ingest_dedup",
            AsyncMock(),
        ),
    ):
        await poll_mailbox("user@example.com", redis=redis, graph_client=graph_client)

    written = redis.set.await_args.args[1]
    assert written == "2026-07-09T12:00:01Z"
    assert written != "2026-07-09T12:00:00Z"


@pytest.mark.asyncio
async def test_run_poll_all_mailboxes_skips_empty() -> None:
    with patch(
        "app.workers.poll_fallback_worker.get_settings",
        return_value=MagicMock(mailbox_list=[]),
    ):
        await run_poll_all_mailboxes()


@pytest.mark.asyncio
async def test_run_poll_all_mailboxes_runs_concurrently() -> None:
    settings = MagicMock(mailbox_list=["a@example.com", "b@example.com"])
    redis = AsyncMock()
    auth = MagicMock()
    graph_client = MagicMock()

    with (
        patch(
            "app.workers.poll_fallback_worker.get_settings",
            return_value=settings,
        ),
        patch(
            "app.workers.poll_fallback_worker.get_redis",
            AsyncMock(return_value=redis),
        ),
        patch(
            "app.workers.poll_fallback_worker.get_graph_auth",
            AsyncMock(return_value=auth),
        ),
        patch(
            "app.workers.poll_fallback_worker.get_graph_client",
            return_value=graph_client,
        ),
        patch(
            "app.workers.poll_fallback_worker.poll_mailbox",
            new_callable=AsyncMock,
        ) as poll,
    ):
        await run_poll_all_mailboxes()

    assert poll.await_count == 2
    mailboxes = {call.kwargs.get("mailbox") or call.args[0] for call in poll.await_args_list}
    assert mailboxes == {"a@example.com", "b@example.com"}
