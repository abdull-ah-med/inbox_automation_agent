"""Re-fetch Graph From name + RFC headers onto already-ingested messages.

Normal poll marks ingest Redis-complete, so existing rows never pick up
``sender_name`` / ``is_automated``. This script GETs each Graph message
(Mail.Read), stamps those columns, and optionally re-runs triage.

Does not send or modify Outlook mail.

Usage (from backend/ or docker exec):

  python -m scripts.backfill_graph_identity --days 7
  python -m scripts.backfill_graph_identity --apply --retry-triage --days 7
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import close_redis, get_graph_auth, get_graph_client, get_redis
from app.core.exceptions import GraphClientError
from app.db.session import dispose_engine, get_session_factory
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.email import EmailDirectionEnum, ThreadStateEnum
from app.models.schemas.graph import IngestResultSchema
from app.repositories import message_repo
from app.services.ingestion_service import (
    _graph_headers_map,
    _to_email_message_schema,
    build_thread_context_from_db,
)

logger = structlog.get_logger(__name__)

_OPEN_STATES = frozenset(
    {
        ThreadStateEnum.NEW.value,
        ThreadStateEnum.DRAFTED.value,
        ThreadStateEnum.AWAITING_CLIENT.value,
        ThreadStateEnum.AWAITING_VENDOR.value,
        ThreadStateEnum.AWAITING_PARTNER.value,
        ThreadStateEnum.REQUIRES_HUMAN.value,
    }
)


async def load_threads_for_window(
    session: AsyncSession,
    *,
    days: int,
    now: datetime,
) -> list[Thread]:
    lookback = now - timedelta(days=days)
    result = await session.execute(
        select(Thread)
        .where(Thread.last_message_at >= lookback)
        .order_by(Thread.last_message_at.asc())
    )
    return list(result.scalars().all())


def _header_names(graph_msg) -> list[str]:
    return sorted(_graph_headers_map(graph_msg).keys())


async def _stamp_thread(*, thread: Thread, graph, apply: bool) -> tuple[int, int]:
    """Return (examined, updated) for one thread."""
    factory = get_session_factory()
    examined = 0
    updated = 0
    async with factory() as session:
        messages = await message_repo.list_by_thread(session, thread.id)

    for row in messages:
        examined += 1
        try:
            graph_msg = await graph.get_message(thread.mailbox, row.graph_message_id)
        except GraphClientError as exc:
            logger.warning(
                "identity_backfill_graph_failed",
                message_id=str(row.id),
                error=str(exc),
            )
            print(f"    graph fail {row.graph_message_id}: {exc}")
            continue

        email = _to_email_message_schema(
            mailbox=thread.mailbox,
            conversation_id=thread.conversation_id,
            message=graph_msg,
        )
        headers = _header_names(graph_msg)
        print(
            f"    {row.direction} {row.sender} "
            f"name={email.sender_display_name!r} automated={email.is_automated} "
            f"headers={headers}"
        )
        if not apply:
            if email.sender_display_name or email.is_automated:
                updated += 1
            continue

        values: dict[str, object] = {}
        if email.sender_display_name and not row.sender_name:
            values["sender_name"] = email.sender_display_name
        if email.is_automated and not row.is_automated:
            values["is_automated"] = True
        if not values:
            continue
        async with factory() as session:
            await session.execute(update(Message).where(Message.id == row.id).values(**values))
            await session.commit()
        updated += 1
    return examined, updated


async def _retry_open_inbound(*, thread: Thread) -> str:
    """Re-run Haiku/draft from the latest inbound. Does not write Outlook mail."""
    from app.services import pipeline_service

    if thread.state not in _OPEN_STATES:
        return f"skipped:state={thread.state}"

    factory = get_session_factory()
    async with factory() as session:
        messages = await message_repo.list_by_thread(session, thread.id)
        if not messages:
            return "skipped:no_messages"
        inbound = max(
            (row for row in messages if row.direction == EmailDirectionEnum.INBOUND.value),
            key=lambda row: row.received_at,
            default=None,
        )
        if inbound is None:
            return "skipped:no_inbound"
        rebuilt = await build_thread_context_from_db(
            session,
            mailbox=thread.mailbox,
            message_id=inbound.graph_message_id,
            resolve_outbound_tip=False,
        )
        if rebuilt is None or rebuilt.thread_context is None:
            return "skipped:rebuild_failed"
        ingest = IngestResultSchema(
            message_id=inbound.graph_message_id,
            status="retry_triage",
            thread_id=str(thread.id),
            conversation_id=thread.conversation_id,
            thread_context=rebuilt.thread_context,
        )

    settings = get_settings()
    redis = await get_redis()
    state = await pipeline_service.run_post_ingest_triage(
        redis=redis,
        settings=settings,
        ingest_result=ingest,
        post_slack=False,
    )
    if state is None:
        return "failed:see_logs"
    needed = None if state.triage is None else state.triage.draft_needed
    return f"done:{state.draft_status} draft_needed={needed}"


async def _load_targets(
    *,
    thread_ids: list[uuid.UUID],
    days: int | None,
) -> list[Thread]:
    factory = get_session_factory()
    async with factory() as session:
        if days is not None:
            rows = await load_threads_for_window(session, days=days, now=datetime.now(UTC))
            for row in rows:
                session.expunge(row)
            return rows
        found: list[Thread] = []
        for thread_id in thread_ids:
            thread = (
                await session.execute(select(Thread).where(Thread.id == thread_id))
            ).scalar_one_or_none()
            if thread is None:
                print(f"thread {thread_id}: NOT FOUND")
                continue
            session.expunge(thread)
            found.append(thread)
        return found


async def _run(
    *,
    thread_ids: list[uuid.UUID],
    days: int | None,
    apply: bool,
    retry_triage: bool,
) -> int:
    redis = await get_redis()
    settings = get_settings()
    auth = await get_graph_auth(settings, redis)
    graph = get_graph_client(auth)
    threads = await _load_targets(thread_ids=thread_ids, days=days)
    print(f"threads={len(threads)} apply={apply} retry_triage={retry_triage}")

    for thread in threads:
        print(f"thread {thread.id} [{thread.mailbox}] {thread.subject!r} state={thread.state}")
        examined, updated = await _stamp_thread(thread=thread, graph=graph, apply=apply)
        print(f"  identity examined={examined} updated={updated} apply={apply}")
        if apply and retry_triage:
            outcome = await _retry_open_inbound(thread=thread)
            print(f"  retry_triage {outcome}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--thread-id",
        action="append",
        help="Thread UUID (repeat for multiple)",
    )
    target.add_argument(
        "--days",
        type=int,
        help="Heal threads whose last_message_at is within this many days",
    )
    parser.add_argument("--apply", action="store_true", help="Write columns (default dry-run)")
    parser.add_argument(
        "--retry-triage",
        action="store_true",
        help="Re-run Haiku/draft for open threads after --apply",
    )
    args = parser.parse_args(argv)
    ids: list[uuid.UUID] = []
    for raw in args.thread_id or []:
        try:
            ids.append(uuid.UUID(raw))
        except ValueError:
            print(f"Invalid --thread-id {raw!r}", file=sys.stderr)
            return 1
    if args.days is not None and args.days < 1:
        print("--days must be >= 1", file=sys.stderr)
        return 1

    async def _main() -> int:
        try:
            return await _run(
                thread_ids=ids,
                days=args.days,
                apply=args.apply,
                retry_triage=args.retry_triage,
            )
        finally:
            await close_redis()
            await dispose_engine()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
