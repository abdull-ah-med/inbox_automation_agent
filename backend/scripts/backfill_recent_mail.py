"""Fetch and ingest recent Graph mail for a demo / catch-up window.

Normal poll only looks back ~15 minutes (or the Redis cursor). This script
forces ``poll_mailbox(..., lookback_override=now - days)`` so older inbox/junk
mail is pulled once through the real ingest → triage → draft path.

Usage (from backend/, with .env loaded):
  python -m scripts.backfill_recent_mail --days 7
  python -m scripts.backfill_recent_mail --days 7 --mailbox inquiries@example.com

Docker Compose:
  docker compose exec backend python -m scripts.backfill_recent_mail --days 7
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

import structlog

from app.core.config import get_settings
from app.core.dependencies import close_redis, get_graph_auth, get_graph_client, get_redis
from app.core.exceptions import GraphClientError
from app.db.session import dispose_engine
from app.workers.poll_fallback_worker import poll_mailbox

logger = structlog.get_logger(__name__)


async def _run(*, days: int, mailbox: str | None) -> int:
    if days < 1:
        print("--days must be >= 1", file=sys.stderr)
        return 1

    settings = get_settings()
    mailboxes = [mailbox] if mailbox else list(settings.mailbox_list)
    if not mailboxes:
        print("No mailboxes configured (set TARGET_MAILBOXES or pass --mailbox)", file=sys.stderr)
        return 1

    lookback = datetime.now(UTC) - timedelta(days=days)
    redis = await get_redis()
    auth = await get_graph_auth(settings, redis)
    graph_client = get_graph_client(auth)

    print(f"Backfilling {len(mailboxes)} mailbox(es) since {lookback.isoformat()}")
    for addr in mailboxes:
        print(f"  polling {addr} ...")
        try:
            await poll_mailbox(
                addr,
                redis=redis,
                graph_client=graph_client,
                lookback_override=lookback,
            )
            print(f"  done {addr}")
        except GraphClientError as exc:
            logger.warning("backfill_mailbox_skipped", mailbox=addr, error=str(exc))
            print(f"  skipped {addr}: {exc}", file=sys.stderr)
        except Exception as exc:
            logger.exception("backfill_mailbox_failed", mailbox=addr)
            print(f"  FAILED {addr}: {exc}", file=sys.stderr)

    await close_redis()
    await dispose_engine()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest Graph mail from the last N days (demo / catch-up)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="How far back to fetch (default: 7)",
    )
    parser.add_argument(
        "--mailbox",
        default=None,
        help="Single mailbox; default is all TARGET_MAILBOXES",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(days=args.days, mailbox=args.mailbox))


if __name__ == "__main__":
    raise SystemExit(main())
