"""Durable Graph webhook Redis Streams — enqueue + consumer ACK."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.redis_keys import (
    GRAPH_WEBHOOK_CONSUMER_GROUP,
    GRAPH_WEBHOOK_STREAM_KEY,
    GRAPH_WEBHOOK_STREAM_MAXLEN,
)
from app.workers.enqueue import enqueue_webhook_job
from app.workers.webhook_stream_worker import (
    ensure_webhook_consumer_group,
    process_stream_once,
)


@pytest.mark.asyncio
async def test_enqueue_webhook_job_xadds_kind_and_json_payload() -> None:
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value=b"1700000000000-0")
    payload = {"value": [{"subscriptionId": "sub-1", "clientState": "secret"}]}

    entry_id = await enqueue_webhook_job(
        redis,
        kind="notifications",
        payload=payload,
    )

    assert entry_id == "1700000000000-0"
    redis.xadd.assert_awaited_once()
    args, kwargs = redis.xadd.await_args
    assert args[0] == GRAPH_WEBHOOK_STREAM_KEY
    fields = args[1]
    assert fields["kind"] == "notifications"
    assert json.loads(fields["payload"]) == payload
    assert kwargs["maxlen"] == GRAPH_WEBHOOK_STREAM_MAXLEN
    assert kwargs["approximate"] is True


@pytest.mark.asyncio
async def test_enqueue_lifecycle_kind() -> None:
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="2-0")
    await enqueue_webhook_job(redis, kind="lifecycle", payload={"value": []})
    assert redis.xadd.await_args.args[1]["kind"] == "lifecycle"


@pytest.mark.asyncio
async def test_ensure_group_ignores_busygroup() -> None:
    redis = AsyncMock()
    from redis.exceptions import ResponseError

    redis.xgroup_create = AsyncMock(
        side_effect=ResponseError("BUSYGROUP Consumer Group name already exists")
    )
    await ensure_webhook_consumer_group(redis)
    redis.xgroup_create.assert_awaited_once()


@pytest.mark.asyncio
async def test_consumer_acks_on_successful_process() -> None:
    """Jobs survive without BackgroundTasks; consumer processes stream entries and XACKs."""
    redis = AsyncMock()
    entry_id = "42-0"
    payload = {"value": [{"subscriptionId": "sub-1", "clientState": "s"}]}
    redis.xreadgroup = AsyncMock(
        return_value=[
            (
                GRAPH_WEBHOOK_STREAM_KEY,
                [
                    (
                        entry_id,
                        {
                            "kind": "notifications",
                            "payload": json.dumps(payload),
                        },
                    )
                ],
            )
        ]
    )
    redis.xack = AsyncMock(return_value=1)

    with patch(
        "app.workers.webhook_stream_worker._handle_entry",
        new_callable=AsyncMock,
    ) as handle:
        handled = await process_stream_once(
            redis,
            consumer="test-consumer",
            settings=MagicMock(),
            claim_stale=False,
        )

    assert handled == 1
    handle.assert_awaited_once()
    redis.xack.assert_awaited_once_with(
        GRAPH_WEBHOOK_STREAM_KEY,
        GRAPH_WEBHOOK_CONSUMER_GROUP,
        entry_id,
    )
