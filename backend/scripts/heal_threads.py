"""Heal stuck NEW threads and open outbound tips. Mail.Read only.

Usage (from backend/):
  .venv/bin/python -m scripts.heal_threads
  .venv/bin/python -m scripts.heal_threads --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog

from app.core.config import get_settings
from app.core.dependencies import close_redis, get_graph_auth, get_graph_client, get_redis
from app.db.session import dispose_engine
from app.workers.heal_threads_worker import run_heal_threads

logger = structlog.get_logger(__name__)


async def _run(*, apply: bool) -> int:
    settings = get_settings()
    redis = await get_redis()
    graph_client = None
    try:
        auth = await get_graph_auth(settings, redis)
        graph_client = get_graph_client(auth)
    except Exception as exc:
        logger.warning("heal_threads_graph_unavailable", error=str(exc))
        print(f"Graph unavailable ({exc}); outbound-tip check skipped.", file=sys.stderr)

    result = await run_heal_threads(
        apply=apply,
        redis=redis,
        settings=settings,
        graph_client=graph_client,
        max_per_run=settings.heal_threads_max_per_run,
    )
    print(
        f"heal_threads apply={apply} examined={result.examined} "
        f"healed={result.healed} stuck_new={result.stuck_new} "
        f"outbound_tip={result.outbound_tip}"
    )
    if not apply:
        print("Dry run only — re-run with --apply to write.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write heals (stuck NEW re-triage, outbound-tip resolve). Default is dry-run.",
    )
    args = parser.parse_args(argv)

    async def _main() -> int:
        try:
            return await _run(apply=args.apply)
        finally:
            await close_redis()
            await dispose_engine()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
