"""One-time backfill for threads stuck at ``NEW`` from before the
``OPENAI_API_KEY``-crash fix (see ``get_openai_client`` in ``core/dependencies``).

Before that fix, every post-ingest pipeline run raised before triage ever
started, so the poll fallback worker still advanced each mailbox's Redis
cursor (``graph:poll:last_checked:<mailbox>``) past these messages'
``receivedDateTime``. That means the normal poll/webhook path will never
refetch them from Graph again — the cursor has already moved on. Because the
messages are already persisted in Postgres, this script re-runs the same
production triage → draft → Slack pipeline (``pipeline_service.
run_post_ingest_triage``) directly from stored data instead of Graph, using
the existing ``retry_triage`` ingest status.

Usage (from backend/):
  .venv/bin/python -m scripts.backfill_stuck_threads          # dry run
  .venv/bin/python -m scripts.backfill_stuck_threads --apply  # actually triage
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import get_settings
from app.core.dependencies import close_redis, get_redis
from app.db.session import dispose_engine, get_session_factory
from app.models.db.thread import Thread
from app.models.schemas.email import (
    EmailDirectionEnum,
    EmailMessageSchema,
    ThreadContextSchema,
    ThreadStateEnum,
)
from app.models.schemas.graph import IngestResultSchema
from app.repositories import message_repo
from app.services import pipeline_service

logger = structlog.get_logger(__name__)


async def _load_stuck_threads() -> list[Thread]:
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Thread).where(Thread.state == ThreadStateEnum.NEW.value)
        result = await session.execute(stmt)
        threads = list(result.scalars().all())
        # Detach from the session before it closes; we only read plain fields.
        for thread in threads:
            session.expunge(thread)
        return threads


async def _backfill_thread(thread: Thread, *, redis: Redis, apply: bool) -> str:
    factory = get_session_factory()
    async with factory() as session:
        messages = await message_repo.list_by_thread(session, thread.id)

    if not messages:
        return "skipped:no_messages"

    email_messages = [
        EmailMessageSchema(
            message_id=m.graph_message_id,
            conversation_id=thread.conversation_id,
            mailbox=thread.mailbox,
            sender=m.sender,
            subject=thread.subject,
            body_text=m.body_text,
            body_preview=m.body_preview,
            received_at=m.received_at,
            direction=EmailDirectionEnum(m.direction),
            to_recipients=list(m.to_recipients),
            cc_recipients=list(m.cc_recipients),
            has_attachments=bool(m.has_attachments),
        )
        for m in messages
    ]
    newest = max(email_messages, key=lambda m: m.received_at)
    context = ThreadContextSchema(
        conversation_id=thread.conversation_id,
        mailbox=thread.mailbox,
        subject=thread.subject,
        messages=email_messages,
    )

    if not apply:
        return f"dry_run:would_triage:{newest.message_id}"

    settings = get_settings()
    ingest_result = IngestResultSchema(
        message_id=newest.message_id,
        status="retry_triage",
        thread_id=str(thread.id),
        conversation_id=thread.conversation_id,
        thread_context=context,
    )
    state = await pipeline_service.run_post_ingest_triage(
        redis=redis,
        settings=settings,
        ingest_result=ingest_result,
        post_slack=True,
    )
    if state is None:
        return "failed:see_logs"
    outcome = "spam" if (state.triage and state.triage.is_spam) else state.draft_status
    return f"done:{outcome}"


async def _run(apply: bool) -> int:
    get_settings()  # ensure env loaded
    threads = await _load_stuck_threads()
    print(f"Found {len(threads)} thread(s) stuck at NEW.")
    if not threads:
        return 0

    redis = await get_redis()
    results: dict[str, int] = {}
    for thread in threads:
        outcome = await _backfill_thread(thread, redis=redis, apply=apply)
        results[outcome] = results.get(outcome, 0) + 1
        print(f"  [{thread.mailbox}] {thread.subject!r} (id={thread.id}) -> {outcome}")

    print("\nSummary:")
    for outcome, count in sorted(results.items()):
        print(f"  {outcome}: {count}")
    if not apply:
        print("\nDry run only — re-run with --apply to actually triage these threads.")

    await close_redis()
    await dispose_engine()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually run triage/draft/Slack. Without this flag, only prints what would happen.",
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args.apply))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
