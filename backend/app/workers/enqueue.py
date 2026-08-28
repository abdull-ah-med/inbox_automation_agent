"""Enqueue helpers for local BackgroundTasks and durable Redis Stream jobs."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any, Literal

from fastapi import BackgroundTasks
from redis.asyncio import Redis

from app.core.redis_keys import (
    GRAPH_WEBHOOK_STREAM_KEY,
    GRAPH_WEBHOOK_STREAM_MAXLEN,
)

WebhookKind = Literal["notifications", "lifecycle"]


def enqueue(
    background_tasks: BackgroundTasks,
    coro_func: Callable[..., Coroutine[Any, Any, Any]],
    *args: Any,
    **kwargs: Any,
) -> None:
    """Schedule coroutine work after the HTTP response is sent (local)."""
    background_tasks.add_task(coro_func, *args, **kwargs)


async def enqueue_webhook_job(
    redis: Redis,
    *,
    kind: WebhookKind,
    payload: dict[str, Any],
) -> str:
    """XADD a Graph webhook job onto the durable stream. Returns the entry id."""
    entry_id = await redis.xadd(
        GRAPH_WEBHOOK_STREAM_KEY,
        {
            "kind": kind,
            "payload": json.dumps(payload, separators=(",", ":"), default=str),
        },
        maxlen=GRAPH_WEBHOOK_STREAM_MAXLEN,
        approximate=True,
    )
    if isinstance(entry_id, bytes):
        return entry_id.decode()
    return str(entry_id)
