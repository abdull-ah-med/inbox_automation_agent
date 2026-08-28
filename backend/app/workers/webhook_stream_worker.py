"""Redis Streams consumer for durable Graph webhook processing.

Ensures the consumer group exists, reads via XREADGROUP, processes with the
existing graph webhook helpers, XACKs on success, and periodically XAUTOCLAIMs
stale pending entries (>60s) so crashed workers do not strand jobs.
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.core.config import Settings, get_settings
from app.core.redis_keys import (
    GRAPH_WEBHOOK_CLAIM_MIN_IDLE_MS,
    GRAPH_WEBHOOK_CONSUMER_GROUP,
    GRAPH_WEBHOOK_STREAM_KEY,
)

logger = structlog.get_logger(__name__)

_READ_COUNT = 10
_BLOCK_MS = 2_000
_CLAIM_EVERY_LOOPS = 15


async def ensure_webhook_consumer_group(redis: Redis) -> None:
    """Create the stream + consumer group if missing (MKSTREAM)."""
    try:
        await redis.xgroup_create(
            GRAPH_WEBHOOK_STREAM_KEY,
            GRAPH_WEBHOOK_CONSUMER_GROUP,
            id="0",
            mkstream=True,
        )
        logger.info(
            "graph_webhook_consumer_group_created",
            stream=GRAPH_WEBHOOK_STREAM_KEY,
            group=GRAPH_WEBHOOK_CONSUMER_GROUP,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _handle_entry(
    *,
    kind: str,
    payload_raw: str,
    settings: Settings,
) -> None:
    # Late import avoids import cycles with the webhook router module.
    from app.api.webhooks.graph import (
        GraphNotificationSchema,
        _process_lifecycle_notifications,
        _process_notifications,
    )

    payload = GraphNotificationSchema.model_validate(json.loads(payload_raw))
    if kind == "lifecycle":
        await _process_lifecycle_notifications(payload, settings)
        return
    if kind == "notifications":
        await _process_notifications(payload, settings)
        return
    logger.warning("graph_webhook_stream_unknown_kind", kind=kind)


def _fields(message: dict[Any, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in message.items():
        k = key.decode() if isinstance(key, bytes) else str(key)
        v = value.decode() if isinstance(value, bytes) else str(value)
        out[k] = v
    return out


async def _claim_stale(redis: Redis, consumer: str) -> list[tuple[str, dict[str, str]]]:
    try:
        claimed = await redis.xautoclaim(
            GRAPH_WEBHOOK_STREAM_KEY,
            GRAPH_WEBHOOK_CONSUMER_GROUP,
            consumer,
            min_idle_time=GRAPH_WEBHOOK_CLAIM_MIN_IDLE_MS,
            start_id="0-0",
            count=_READ_COUNT,
        )
    except ResponseError as exc:
        logger.warning("graph_webhook_xautoclaim_unavailable", error=str(exc))
        return []

    messages: list[Any] = (
        claimed[1] or [] if isinstance(claimed, (list, tuple)) and len(claimed) >= 2 else []
    )

    result: list[tuple[str, dict[str, str]]] = []
    for entry_id, data in messages:
        eid = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
        result.append((eid, _fields(data or {})))
    return result


async def process_stream_once(
    redis: Redis,
    *,
    consumer: str,
    settings: Settings | None = None,
    claim_stale: bool = False,
) -> int:
    """Read/claim up to a batch of jobs, process, and XACK. Returns handled count."""
    cfg = settings or get_settings()
    handled = 0
    batch: list[tuple[str, dict[str, str]]] = []

    if claim_stale:
        batch.extend(await _claim_stale(redis, consumer))

    rows = await redis.xreadgroup(
        groupname=GRAPH_WEBHOOK_CONSUMER_GROUP,
        consumername=consumer,
        streams={GRAPH_WEBHOOK_STREAM_KEY: ">"},
        count=_READ_COUNT,
        block=_BLOCK_MS,
    )
    if rows:
        for _stream, messages in rows:
            for entry_id, data in messages:
                eid = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
                batch.append((eid, _fields(data or {})))

    for entry_id, fields in batch:
        kind = fields.get("kind", "")
        payload_raw = fields.get("payload", "")
        try:
            await _handle_entry(kind=kind, payload_raw=payload_raw, settings=cfg)
            await redis.xack(
                GRAPH_WEBHOOK_STREAM_KEY,
                GRAPH_WEBHOOK_CONSUMER_GROUP,
                entry_id,
            )
            handled += 1
        except Exception:
            logger.exception(
                "graph_webhook_stream_job_failed",
                entry_id=entry_id,
                kind=kind,
            )
    return handled


async def run_webhook_stream_worker(
    redis: Redis,
    *,
    stop_event: asyncio.Event | None = None,
    consumer_name: str | None = None,
) -> None:
    """Long-running consumer loop. Cancels cleanly when ``stop_event`` is set."""
    await ensure_webhook_consumer_group(redis)
    consumer = consumer_name or f"webhook-{socket.gethostname()}-{id(asyncio.current_task())}"
    stop = stop_event or asyncio.Event()
    loops = 0
    logger.info("graph_webhook_stream_worker_started", consumer=consumer)
    while not stop.is_set():
        loops += 1
        try:
            await process_stream_once(
                redis,
                consumer=consumer,
                claim_stale=(loops % _CLAIM_EVERY_LOOPS == 0),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("graph_webhook_stream_worker_loop_error")
            await asyncio.sleep(1)
    logger.info("graph_webhook_stream_worker_stopped", consumer=consumer)
