"""One-shot EC2 heal: unresolved tip sends + missing meeting labels.

1. Sent Items lookback (heals Redis-complete outbound that never resolved).
2. Meeting type backfill (stamps Graph meetingMessageType for UI labels).
3. DB tip scan: open threads whose newest message is outbound get resolve
   even when Graph Sent Items no longer returns that id.

Usage (from backend/ or docker exec):
  python -m scripts.repair_sent_and_meetings --days 7 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from app.core.config import get_settings
from app.core.dependencies import close_redis, get_graph_auth, get_graph_client, get_redis
from app.core.exceptions import GraphClientError
from app.db.session import dispose_engine, get_session_factory
from app.models.db.thread import Thread
from app.models.schemas.email import EmailDirectionEnum, ThreadStateEnum
from app.repositories import message_repo, sent_reply_repo
from app.repositories.message_repo import MessageSchema
from app.services import sent_reply_service
from app.workers.poll_fallback_worker import poll_mailbox
from scripts import backfill_meeting_messages

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


async def _backfill_sent_items(*, days: int) -> None:
    settings = get_settings()
    mailboxes = list(settings.mailbox_list)
    lookback = datetime.now(UTC) - timedelta(days=days)
    redis = await get_redis()
    auth = await get_graph_auth(settings, redis)
    graph_client = get_graph_client(auth)
    print(
        f"Backfilling {len(mailboxes)} mailbox(es) since {lookback.isoformat()} "
        "— Sent Items (outbound resolve)"
    )
    for addr in mailboxes:
        print(f"  polling {addr} ...")
        try:
            await poll_mailbox(
                addr,
                redis=redis,
                graph_client=graph_client,
                lookback_override=lookback,
                folders=("sentitems",),
                outbound_only=True,
            )
            print(f"  done {addr}")
        except GraphClientError as exc:
            logger.warning("repair_sent_mailbox_skipped", mailbox=addr, error=str(exc))
            print(f"  skipped {addr}: {exc}", file=sys.stderr)
        except Exception as exc:
            logger.exception("repair_sent_mailbox_failed", mailbox=addr)
            print(f"  FAILED {addr}: {exc}", file=sys.stderr)


async def _repair_tip_outbound(*, apply: bool) -> tuple[int, int]:
    """Return (examined, healed) for open threads whose tip is outbound."""
    factory = get_session_factory()
    examined = 0
    healed = 0

    async with factory() as session:
        threads = list(
            (
                await session.execute(
                    select(Thread)
                    .where(Thread.state.in_(list(_OPEN_STATES)))
                    .order_by(Thread.last_message_at.desc())
                    .limit(500)
                )
            )
            .scalars()
            .all()
        )

    for thread in threads:
        examined += 1
        async with factory() as session:
            rows = await message_repo.list_by_thread(session, thread.id)
            if not rows:
                continue
            tip: MessageSchema = max(rows, key=lambda row: row.received_at)
            if tip.direction != EmailDirectionEnum.OUTBOUND.value:
                continue
            if sent_reply_service.is_meeting_message(tip):
                continue
            existing = await sent_reply_repo.get_by_thread(session, thread.id)
            if existing is not None:
                continue
            logger.info(
                "repair_tip_outbound_candidate",
                thread_id=str(thread.id),
                mailbox=thread.mailbox,
                tip_graph_message_id=tip.graph_message_id,
                state=thread.state,
                apply=apply,
            )
            if not apply:
                healed += 1
                continue
            await sent_reply_service.resolve_thread_from_outbound(
                session,
                thread_id=thread.id,
                message=tip,
                conversation_id=thread.conversation_id,
                mailbox=thread.mailbox,
            )
            await session.commit()
            healed += 1

    return examined, healed


async def _run(*, days: int, apply: bool) -> int:
    if days < 1:
        print("--days must be >= 1", file=sys.stderr)
        return 1

    await _backfill_sent_items(days=days)

    settings = get_settings()
    redis = await get_redis()
    auth = await get_graph_auth(settings, redis)
    graph = get_graph_client(auth)
    m_ex, m_up = await backfill_meeting_messages._backfill_meeting_types(
        graph=graph,
        apply=apply,
    )
    print(f"meeting types: examined={m_ex} updated={m_up} apply={apply}")

    t_ex, t_up = await _repair_tip_outbound(apply=apply)
    print(f"tip outbound: examined={t_ex} healed={t_up} apply={apply}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="Sent Items lookback days")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write meeting stamps + tip resolves (Sent Items always applies)",
    )
    args = parser.parse_args(argv)

    async def _main() -> int:
        try:
            return await _run(days=args.days, apply=args.apply)
        finally:
            await close_redis()
            await dispose_engine()

    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
