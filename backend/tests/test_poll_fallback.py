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
        patch(
            "app.workers.poll_fallback_worker.pipeline_service.run_post_ingest_triage",
            AsyncMock(return_value=MagicMock()),
        ) as triage,
    ):
        await poll_mailbox(
            "user@example.com",
            redis=redis,
            graph_client=graph_client,
        )

    assert graph_client.list_messages.await_count == 2
    folders = [c.kwargs["folder"] for c in graph_client.list_messages.await_args_list]
    assert folders == ["inbox", "junkemail"]
    kwargs = graph_client.list_messages.await_args_list[0].kwargs
    assert "receivedDateTime ge " in kwargs["filter_query"]
    assert kwargs["follow_next_link"] is True
    ingest.assert_awaited_once()
    triage.assert_awaited_once()
    complete.assert_awaited_once_with(redis, "user@example.com", "msg-1")
    redis.set.assert_awaited()
    assert redis.set.await_args.args[0] == poll_cursor_key("user@example.com")
    assert redis.set.await_args.args[1] == "2026-07-09T12:00:01Z"


@pytest.mark.asyncio
async def test_poll_mailbox_passes_junk_folder_to_ingest() -> None:
    """Junk-only mail must be labeled junkemail so triage can treat Outlook as a hint."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)

    junk_msg = GraphMessageSchema.model_validate(
        {
            "id": "junk-1",
            "subject": "SampleLab invoice",
            "receivedDateTime": "2026-08-20T12:00:00Z",
            "conversationId": "conv-junk",
            "from": {"emailAddress": {"address": "orders@sample-lab.example.com"}},
        }
    )

    async def _list_messages(_mailbox: str, **kwargs: object) -> list[GraphMessageSchema]:
        if kwargs.get("folder") == "junkemail":
            return [junk_msg]
        return []

    graph_client = MagicMock()
    graph_client.list_messages = AsyncMock(side_effect=_list_messages)

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
            AsyncMock(return_value=IngestResultSchema(message_id="junk-1", status="ingested")),
        ) as ingest,
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.complete_ingest_dedup",
            AsyncMock(),
        ),
        patch(
            "app.workers.poll_fallback_worker.pipeline_service.run_post_ingest_triage",
            AsyncMock(return_value=MagicMock()),
        ),
    ):
        await poll_mailbox(
            "elise@sample-site.example.com",
            redis=redis,
            graph_client=graph_client,
        )

    assert ingest.await_args.kwargs["source_folder"] == "junkemail"
    assert ingest.await_args.kwargs["message_id"] == "junk-1"


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
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.release_ingest_dedup",
            AsyncMock(),
        ),
        patch(
            "app.workers.poll_fallback_worker.pipeline_service.run_post_ingest_triage",
            AsyncMock(return_value=MagicMock()),
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

    assert graph_client.list_messages.await_count == 2
    for call in graph_client.list_messages.await_args_list:
        assert call.kwargs["filter_query"] == "receivedDateTime ge 2026-07-09T10:00:00Z"
    folders = [c.kwargs["folder"] for c in graph_client.list_messages.await_args_list]
    assert folders == ["inbox", "junkemail"]


@pytest.mark.asyncio
async def test_poll_mailbox_ingests_junk_only_messages() -> None:
    """Junk-folder mail must be ingested even when Inbox is empty."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)

    junk_msg = GraphMessageSchema.model_validate(
        {
            "id": "junk-1",
            "subject": "Spammy",
            "receivedDateTime": "2026-07-09T12:00:00Z",
            "conversationId": "conv-junk",
            "from": {"emailAddress": {"address": "spam@b.com"}},
        }
    )

    async def _list_messages(*_args: object, folder: str = "inbox", **_kwargs: object):
        if folder == "junkemail":
            return [junk_msg]
        return []

    graph_client = MagicMock()
    graph_client.list_messages = AsyncMock(side_effect=_list_messages)

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
            AsyncMock(return_value=IngestResultSchema(message_id="junk-1", status="ingested")),
        ) as ingest,
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.complete_ingest_dedup",
            AsyncMock(),
        ),
        patch(
            "app.workers.poll_fallback_worker.pipeline_service.run_post_ingest_triage",
            AsyncMock(return_value=MagicMock()),
        ),
    ):
        await poll_mailbox(
            "user@example.com",
            redis=redis,
            graph_client=graph_client,
        )

    ingest.assert_awaited_once()
    assert ingest.await_args.kwargs["message_id"] == "junk-1"
    assert redis.set.await_args.args[1] == "2026-07-09T12:00:01Z"


@pytest.mark.asyncio
async def test_poll_mailbox_access_denied_does_not_raise() -> None:
    """Graph 403 on every folder must skip the mailbox without crashing."""
    from app.core.exceptions import GraphClientError

    redis = AsyncMock()
    redis.get = AsyncMock(return_value="2026-07-09T10:00:00Z")
    redis.set = AsyncMock(return_value=True)
    graph_client = MagicMock()
    graph_client.list_messages = AsyncMock(
        side_effect=GraphClientError(
            "Graph API GET failed with status 403: "
            '{"error":{"code":"ErrorAccessDenied","message":"Access is denied."}}'
        )
    )

    await poll_mailbox(
        "denied@example.com",
        redis=redis,
        graph_client=graph_client,
    )

    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_mailbox_continues_when_one_folder_denied() -> None:
    """Inbox access denied must not block junk-folder ingest."""
    from app.core.exceptions import GraphClientError

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)

    junk_msg = GraphMessageSchema.model_validate(
        {
            "id": "junk-1",
            "subject": "Spammy",
            "receivedDateTime": "2026-07-09T12:00:00Z",
            "conversationId": "conv-junk",
            "from": {"emailAddress": {"address": "spam@b.com"}},
        }
    )

    async def _list_messages(*_args: object, folder: str = "inbox", **_kwargs: object):
        if folder == "inbox":
            raise GraphClientError(
                "Graph API GET failed with status 403: "
                '{"error":{"code":"ErrorAccessDenied","message":"Access is denied."}}'
            )
        return [junk_msg]

    graph_client = MagicMock()
    graph_client.list_messages = AsyncMock(side_effect=_list_messages)

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
            AsyncMock(return_value=IngestResultSchema(message_id="junk-1", status="ingested")),
        ) as ingest,
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.complete_ingest_dedup",
            AsyncMock(),
        ),
        patch(
            "app.workers.poll_fallback_worker.pipeline_service.run_post_ingest_triage",
            AsyncMock(return_value=MagicMock()),
        ),
    ):
        await poll_mailbox(
            "user@example.com",
            redis=redis,
            graph_client=graph_client,
        )

    ingest.assert_awaited_once()
    assert ingest.await_args.kwargs["message_id"] == "junk-1"


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
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.release_ingest_dedup",
            AsyncMock(),
        ),
        patch(
            "app.workers.poll_fallback_worker.pipeline_service.run_post_ingest_triage",
            AsyncMock(return_value=MagicMock()),
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
        patch(
            "app.workers.poll_fallback_worker.pipeline_service.run_post_ingest_triage",
            AsyncMock(return_value=MagicMock()),
        ),
    ):
        await poll_mailbox("user@example.com", redis=redis, graph_client=graph_client)

    written = redis.set.await_args.args[1]
    assert written == "2026-07-09T12:00:01Z"
    assert written != "2026-07-09T12:00:00Z"


@pytest.mark.asyncio
async def test_poll_mailbox_empty_keeps_existing_cursor() -> None:
    """Empty Graph page must not jump watermark to now (message-loss risk)."""
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

    assert redis.set.await_args.args[1] == "2026-07-09T10:00:00Z"


@pytest.mark.asyncio
async def test_poll_mailbox_in_flight_does_not_advance_cursor() -> None:
    """processing claim without completed triage must not move the watermark."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="2026-07-09T10:00:00Z")
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
            AsyncMock(return_value=IngestResultSchema(message_id="msg-1", status="in_flight")),
        ),
    ):
        await poll_mailbox(
            "user@example.com",
            redis=redis,
            graph_client=graph_client,
        )

    assert redis.set.await_args.args[1] == "2026-07-09T10:00:00Z"


@pytest.mark.asyncio
async def test_poll_mailbox_outbound_only_uses_sentitems_cursor() -> None:
    """Sent Items polls write a separate Redis watermark and skip triage."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    msg = GraphMessageSchema.model_validate(
        {
            "id": "sent-1",
            "subject": "Re: Hello",
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
            "app.workers.poll_fallback_worker.ingestion_service.handle_outbound_notification",
            AsyncMock(return_value=IngestResultSchema(message_id="sent-1", status="outbound")),
        ) as outbound,
        patch(
            "app.workers.poll_fallback_worker.ingestion_service.ingest_graph_message",
            new_callable=AsyncMock,
        ) as ingest,
        patch(
            "app.workers.poll_fallback_worker.pipeline_service.run_post_ingest_triage",
            new_callable=AsyncMock,
        ) as triage,
    ):
        await poll_mailbox(
            "user@example.com",
            redis=redis,
            graph_client=graph_client,
            folders=("sentitems",),
            outbound_only=True,
        )

    folders = [c.kwargs["folder"] for c in graph_client.list_messages.await_args_list]
    assert folders == ["sentitems"]
    outbound.assert_awaited_once()
    ingest.assert_not_awaited()
    triage.assert_not_awaited()
    assert redis.set.await_args.args[0] == poll_cursor_key("user@example.com", "sentitems")
    assert redis.set.await_args.args[1] == "2026-07-09T12:00:01Z"


@pytest.mark.asyncio
async def test_poll_mailbox_outbound_reads_sentitems_cursor() -> None:
    redis = AsyncMock()

    async def _get(key: str) -> str | None:
        if key == poll_cursor_key("user@example.com", "sentitems"):
            return "2026-07-09T10:00:00Z"
        return None

    redis.get = AsyncMock(side_effect=_get)
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
            folders=("sentitems",),
            outbound_only=True,
        )

    redis.get.assert_awaited_with(poll_cursor_key("user@example.com", "sentitems"))
    assert graph_client.list_messages.await_args.kwargs["filter_query"] == (
        "receivedDateTime ge 2026-07-09T10:00:00Z"
    )
    assert redis.set.await_args.args[0] == poll_cursor_key("user@example.com", "sentitems")


@pytest.mark.asyncio
async def test_run_poll_all_mailboxes_skips_empty() -> None:
    with patch(
        "app.workers.poll_fallback_worker.get_settings",
        return_value=MagicMock(mailbox_list=[]),
    ):
        await run_poll_all_mailboxes()


@pytest.mark.asyncio
async def test_run_poll_all_mailboxes_runs_concurrently() -> None:
    settings = MagicMock(
        mailbox_list=["a@example.com", "b@example.com"],
        reviewer_mailbox_list=[],
        poll_concurrency=3,
        poll_interval_seconds=300,
    )
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
            "app.workers.poll_fallback_worker.acquire_lock",
            AsyncMock(return_value="owner-token"),
        ),
        patch(
            "app.workers.poll_fallback_worker.extend_lock",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.workers.poll_fallback_worker.release_lock",
            AsyncMock(return_value=True),
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

    # Each mailbox: inbound poll + Sent Items outbound poll.
    assert poll.await_count == 4
    mailboxes = {call.kwargs.get("mailbox") or call.args[0] for call in poll.await_args_list}
    assert mailboxes == {"a@example.com", "b@example.com"}
    outbound_calls = [
        call for call in poll.await_args_list if call.kwargs.get("outbound_only") is True
    ]
    assert len(outbound_calls) == 2
    for call in outbound_calls:
        assert call.kwargs["folders"] == ("sentitems",)


@pytest.mark.asyncio
async def test_run_poll_all_mailboxes_includes_reviewer_sent_items() -> None:
    """Elise-style REVIEWER_MAILBOXES must be polled for Sent Items."""
    settings = MagicMock(
        mailbox_list=["inquiries@example.com"],
        reviewer_mailbox_list=["elise@example.com"],
        poll_concurrency=2,
        poll_interval_seconds=300,
    )
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
            "app.workers.poll_fallback_worker.acquire_lock",
            AsyncMock(return_value="token"),
        ),
        patch(
            "app.workers.poll_fallback_worker.extend_lock",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.workers.poll_fallback_worker.release_lock",
            AsyncMock(return_value=True),
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

    # Target: inbound + outbound. Reviewer: outbound only.
    assert poll.await_count == 3
    reviewer_calls = [
        call
        for call in poll.await_args_list
        if (call.kwargs.get("mailbox") or call.args[0]) == "elise@example.com"
    ]
    assert len(reviewer_calls) == 1
    assert reviewer_calls[0].kwargs["outbound_only"] is True
    assert reviewer_calls[0].kwargs["folders"] == ("sentitems",)


@pytest.mark.asyncio
async def test_run_poll_all_mailboxes_outbound_runs_after_inbound_failure() -> None:
    """Inbound exception must not skip the Sent Items poll for that mailbox."""
    settings = MagicMock(
        mailbox_list=["a@example.com"],
        reviewer_mailbox_list=[],
        poll_concurrency=3,
        poll_interval_seconds=300,
    )
    redis = AsyncMock()
    auth = MagicMock()
    graph_client = MagicMock()
    calls: list[bool] = []

    async def _poll(*_args: object, **kwargs: object) -> None:
        outbound = bool(kwargs.get("outbound_only"))
        calls.append(outbound)
        if not outbound:
            raise RuntimeError("inbound boom")

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
            "app.workers.poll_fallback_worker.acquire_lock",
            AsyncMock(return_value="owner-token"),
        ),
        patch(
            "app.workers.poll_fallback_worker.extend_lock",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.workers.poll_fallback_worker.release_lock",
            AsyncMock(return_value=True),
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
            side_effect=_poll,
        ),
    ):
        await run_poll_all_mailboxes()

    assert calls == [False, True]


@pytest.mark.asyncio
async def test_run_poll_all_mailboxes_skips_when_not_leader() -> None:
    settings = MagicMock(
        mailbox_list=["a@example.com"],
        reviewer_mailbox_list=[],
        poll_concurrency=3,
        poll_interval_seconds=300,
    )
    redis = AsyncMock()

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
            "app.workers.poll_fallback_worker.acquire_lock",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.workers.poll_fallback_worker.poll_mailbox",
            new_callable=AsyncMock,
        ) as poll,
    ):
        await run_poll_all_mailboxes()

    poll.assert_not_awaited()
