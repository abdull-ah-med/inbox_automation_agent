"""Poll fallback worker — catch messages missed by Graph webhooks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.dependencies import get_graph_auth, get_graph_client, get_redis
from app.core.redis_keys import (
    DEFAULT_POLL_LOOKBACK_SECONDS,
    SCHEDULER_POLL_LOCK_KEY,
    poll_cursor_key,
)
from app.core.redis_lock import acquire_lock
from app.db.session import get_session_factory
from app.graph.client import GraphClient
from app.models.schemas.graph import GraphMessageSchema
from app.services import ingestion_service, pipeline_service

logger = structlog.get_logger(__name__)

_TRIAGE_ELIGIBLE = frozenset({"ingested", "retry_triage"})
# Well-known Graph folder names. Poller covers Inbox + Junk (spam often lands here).
_POLL_FOLDERS: tuple[str, ...] = ("inbox", "junkemail")


def _to_graph_datetime(value: datetime) -> str:
    """Format UTC datetime for Graph OData $filter (YYYY-MM-DDTHH:MM:SSZ)."""
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _read_cursor(redis: Redis, mailbox: str) -> datetime | None:
    raw = await redis.get(poll_cursor_key(mailbox))
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


async def _write_cursor(redis: Redis, mailbox: str, value: datetime) -> None:
    await redis.set(poll_cursor_key(mailbox), _to_graph_datetime(value))


def _normalize_received(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _message_sort_key(message: GraphMessageSchema) -> datetime:
    received = _normalize_received(message.received_date_time)
    return received if received is not None else datetime.min.replace(tzinfo=UTC)


async def _collect_poll_messages(
    mailbox: str,
    *,
    graph_client: GraphClient,
    filter_query: str,
) -> list[GraphMessageSchema]:
    """List messages from all poll folders, dedupe by id, sort by received time."""
    messages: list[GraphMessageSchema] = []
    seen_ids: set[str] = set()
    for folder in _POLL_FOLDERS:
        folder_messages = await graph_client.list_messages(
            mailbox,
            folder=folder,
            filter_query=filter_query,
            orderby="receivedDateTime asc",
            top=50,
            follow_next_link=True,
        )
        logger.info(
            "poll_folder_fetched",
            mailbox=mailbox,
            folder=folder,
            message_count=len(folder_messages),
        )
        for message in folder_messages:
            if message.id in seen_ids:
                continue
            seen_ids.add(message.id)
            messages.append(message)
    messages.sort(key=_message_sort_key)
    return messages


async def poll_mailbox(
    mailbox: str,
    *,
    redis: Redis,
    graph_client: GraphClient,
    lookback_override: datetime | None = None,
) -> None:
    """Poll inbox + junk messages since last_checked (or lookback) and ingest each.

    Cursor advances only through contiguous successes from the start of the
    ordered batch. A failed / in-flight message stops cursor advancement so it
    is retried on the next poll (``receivedDateTime ge``). Later messages may
    still be attempted; Redis dedup prevents double-persist of successes.

    Empty pages do **not** jump the cursor to ``now`` when a cursor already
    exists — an empty page is not proof that nothing will appear with the same
    lower bound (Graph paging / eventual consistency).

    OData ``ge`` is inclusive. After a fully successful batch, advance the
    watermark by 1 second past the last processed message so the next poll does
    not re-fetch that message. On partial failure, keep the exact success
    timestamp so same-second retries remain possible.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    existing_cursor = await _read_cursor(redis, mailbox)

    if lookback_override is not None:
        since = lookback_override
    elif existing_cursor is not None:
        since = existing_cursor
    else:
        since = now - timedelta(seconds=DEFAULT_POLL_LOOKBACK_SECONDS)

    filter_query = f"receivedDateTime ge {_to_graph_datetime(since)}"
    logger.info(
        "poll_mailbox_start",
        mailbox=mailbox,
        since=_to_graph_datetime(since),
        folders=_POLL_FOLDERS,
    )

    messages = await _collect_poll_messages(
        mailbox,
        graph_client=graph_client,
        filter_query=filter_query,
    )

    cursor_candidate = existing_cursor
    advance_cursor = True
    session_factory = get_session_factory()

    for message in messages:
        try:
            async with session_factory() as session, session.begin():
                result = await ingestion_service.ingest_graph_message(
                    session=session,
                    redis=redis,
                    graph_client=graph_client,
                    mailbox=mailbox,
                    message_id=message.id,
                )
            if result.status in _TRIAGE_ELIGIBLE:
                claimed = await ingestion_service.claim_triage_lock(redis, mailbox, message.id)
                if not claimed:
                    logger.info(
                        "poll_triage_skipped_lock_held",
                        mailbox=mailbox,
                        message_id=message.id,
                    )
                    advance_cursor = False
                    continue
                try:
                    triage_state = await pipeline_service.run_post_ingest_triage(
                        redis=redis,
                        settings=settings,
                        ingest_result=result,
                    )
                    if triage_state is not None:
                        await ingestion_service.complete_ingest_dedup(redis, mailbox, message.id)
                    else:
                        await ingestion_service.release_ingest_dedup(redis, mailbox, message.id)
                        advance_cursor = False
                        continue
                finally:
                    await ingestion_service.release_triage_lock(redis, mailbox, message.id, claimed)
            elif result.status == "in_flight" or result.status == "skipped":
                advance_cursor = False
                continue
        except Exception:
            # Triage lock (if claimed) is released in the inner ``finally`` with
            # the owner token — do not unconditional DEL here.
            await ingestion_service.release_ingest_dedup(redis, mailbox, message.id)
            logger.exception(
                "poll_ingest_failed",
                mailbox=mailbox,
                message_id=message.id,
            )
            advance_cursor = False
            continue

        if advance_cursor:
            received = _normalize_received(message.received_date_time)
            if received is not None and (cursor_candidate is None or received > cursor_candidate):
                cursor_candidate = received

    if not messages:
        new_cursor = existing_cursor if existing_cursor is not None else now
    elif advance_cursor and cursor_candidate is not None:
        new_cursor = cursor_candidate + timedelta(seconds=1)
    elif cursor_candidate is not None:
        new_cursor = cursor_candidate
    else:
        new_cursor = existing_cursor if existing_cursor is not None else since

    if existing_cursor is None or new_cursor >= existing_cursor:
        await _write_cursor(redis, mailbox, new_cursor)

    logger.info(
        "poll_mailbox_complete",
        mailbox=mailbox,
        message_count=len(messages),
        cursor=_to_graph_datetime(new_cursor),
        cursor_advanced_fully=advance_cursor,
    )


async def run_poll_all_mailboxes() -> None:
    """Poll every configured mailbox concurrently (bounded by a semaphore).

    Uses a Redis NX lock so only one Uvicorn worker runs the poll interval.
    Uses the process-scoped GraphClient singleton — do not aclose it here; the
    FastAPI lifespan closes the shared pool on shutdown.
    """
    settings = get_settings()
    if not settings.mailbox_list:
        logger.info("poll_no_mailboxes")
        return

    redis = await get_redis()
    lock_ttl = max(settings.poll_interval_seconds - 5, 30)
    # Owner-token NX lock (Redis lock pattern) — TTL alone releases; no unsafe DEL.
    # https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/
    token = await acquire_lock(redis, SCHEDULER_POLL_LOCK_KEY, ttl_seconds=lock_ttl)
    if token is None:
        logger.info("poll_skipped_not_leader")
        return

    auth = await get_graph_auth(settings, redis)
    graph_client = get_graph_client(auth)
    semaphore = asyncio.Semaphore(settings.poll_concurrency)

    async def _poll_one(mailbox: str) -> None:
        async with semaphore:
            try:
                await poll_mailbox(mailbox, redis=redis, graph_client=graph_client)
            except Exception:
                logger.exception("poll_mailbox_failed", mailbox=mailbox)

    await asyncio.gather(*(_poll_one(mailbox) for mailbox in settings.mailbox_list))
