"""Re-run phased triage/draft for RESOLVED threads whose tip is a newer inbound.

Use after the tip-relative already-replied fix to reopen follow-ups that were
stuck RESOLVED under the old forever-sent_reply gate.

Does not send or modify Outlook mail (Mail.Read only).

Usage (from backend/, with .env loaded):

  # Dry run — list stuck threads only
  .venv/bin/python -m scripts.reopen_followup_inbounds

  # Single thread
  .venv/bin/python -m scripts.reopen_followup_inbounds --apply --thread-id <uuid>

  # All stuck threads (cap with --limit)
  .venv/bin/python -m scripts.reopen_followup_inbounds --apply --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select

from app.core.config import get_settings
from app.core.dependencies import (
    anthropic_client_from_settings,
    close_openai_client,
    get_redis,
    openai_client_from_settings,
)
from app.db.session import dispose_engine, get_session_factory
from app.models.db.sent_reply import SentReply
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.repositories import message_repo, thread_repo
from app.services import ingestion_service
from app.services.pipeline import service as pipeline_service
from app.services.sent_reply_service import thread_tip_already_replied

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _StuckThread:
    id: uuid.UUID
    mailbox: str
    subject: str
    tip_graph_message_id: str


async def _list_stuck(*, thread_id: uuid.UUID | None, limit: int) -> list[_StuckThread]:
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Thread).where(Thread.state == ThreadStateEnum.RESOLVED.value)
        if thread_id is not None:
            stmt = stmt.where(Thread.id == thread_id)
        stmt = stmt.order_by(Thread.last_message_at.desc().nullslast()).limit(limit * 5)
        threads = list((await session.execute(stmt)).scalars().all())

        stuck: list[_StuckThread] = []
        for thread in threads:
            if len(stuck) >= limit:
                break
            has_sent = (
                await session.execute(
                    select(SentReply.id).where(SentReply.thread_id == thread.id).limit(1)
                )
            ).scalar_one_or_none()
            if has_sent is None:
                continue
            if await thread_tip_already_replied(session, thread.id):
                continue
            messages = await message_repo.list_by_thread(session, thread.id)
            if not messages:
                continue
            tip = max(messages, key=lambda m: m.received_at)
            if tip.direction != "inbound":
                continue
            stuck.append(
                _StuckThread(
                    id=thread.id,
                    mailbox=thread.mailbox,
                    subject=thread.subject,
                    tip_graph_message_id=tip.graph_message_id,
                )
            )
        return stuck


async def _reopen_one(item: _StuckThread, *, apply: bool) -> str:
    if not apply:
        return "dry_run:would_reopen"

    settings = get_settings()
    factory = get_session_factory()
    redis = await get_redis()
    openai = openai_client_from_settings(settings)
    anthropic = anthropic_client_from_settings(settings)

    async with factory() as session:
        rebuilt = await ingestion_service.build_thread_context_from_db(
            session,
            mailbox=item.mailbox,
            message_id=item.tip_graph_message_id,
        )
        if session.in_transaction():
            await session.commit()

    if rebuilt is None:
        return "skip:no_thread_context"
    if rebuilt.status == "outbound":
        return "skip:tip_outbound"

    state = await pipeline_service.run_phased_after_ingest(
        redis=redis,
        settings=settings,
        ingest_result=rebuilt,
        session_factory=factory,
        openai_client=openai,
        client=anthropic,
        post_slack=False,
    )

    async with factory() as session:
        thread = await thread_repo.get_by_id_trusted(session, item.id)
        new_state = thread.state if thread is not None else "?"

    return f"ok:draft_status={state.draft_status}:state={new_state}"


async def _run(*, apply: bool, thread_id: uuid.UUID | None, limit: int) -> int:
    stuck = await _list_stuck(thread_id=thread_id, limit=limit)
    if not stuck:
        print("No stuck follow-up threads found.")
        return 0

    print(f"Found {len(stuck)} stuck thread(s); apply={apply}")
    failures = 0
    for item in stuck:
        try:
            outcome = await _reopen_one(item, apply=apply)
            print(f"{item.id}  {item.mailbox}  {outcome}  {item.subject[:60]}")
            if outcome.startswith(("skip", "dry_run")):
                continue
            if not outcome.startswith("ok:"):
                failures += 1
        except Exception:
            failures += 1
            logger.exception("reopen_followup_failed", thread_id=str(item.id))
            print(f"{item.id}  FAILED", file=sys.stderr)

    await close_openai_client()
    await dispose_engine()
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Run pipeline (default is dry-run)")
    parser.add_argument("--thread-id", type=uuid.UUID, default=None)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(_run(apply=args.apply, thread_id=args.thread_id, limit=args.limit))
    )


if __name__ == "__main__":
    main()
