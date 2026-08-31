"""Re-generate drafts (and suggested_actions) for threads that already have one.

Use after uploading/activating a skill so past threads pick it up on a new
draft row. Regenerating runs skill selection + Sonnet again; Suggested Process
(``suggested_actions``), draft body, urgency, and ``applied_skills`` all refresh
together. Does not send or modify Outlook mail.

Usage (from backend/, with .env loaded):

  # Dry run — list candidates only
  .venv/bin/python -m scripts.regenerate_drafts

  # Apply to up to 5 threads in one mailbox
  .venv/bin/python -m scripts.regenerate_drafts --apply --mailbox inquiries@example.com --limit 5

  # Single thread
  .venv/bin/python -m scripts.regenerate_drafts --apply --thread-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import distinct, select

from app.core.config import get_settings
from app.core.dependencies import (
    anthropic_client_from_settings,
    close_openai_client,
    openai_client_from_settings,
)
from app.core.exceptions import DraftGenerationError, ThreadNotFoundError
from app.db.session import dispose_engine, get_session_factory
from app.models.db.draft import Draft
from app.models.db.thread import Thread
from app.services import draft_regeneration_service

logger = structlog.get_logger(__name__)

_DEFAULT_INSTRUCTION = (
    "Re-draft using active process skills where they apply. "
    "Refresh suggested_actions to match the correct workflow."
)


@dataclass(frozen=True)
class _Candidate:
    id: uuid.UUID
    mailbox: str
    subject: str
    category: str | None


async def _load_candidates(
    *,
    mailbox: str | None,
    thread_id: uuid.UUID | None,
    limit: int,
    allowed_mailboxes: list[str],
) -> list[_Candidate]:
    factory = get_session_factory()
    async with factory() as session:
        if thread_id is not None:
            thread = await session.get(Thread, thread_id)
            if thread is None:
                return []
            session.expunge(thread)
            return [
                _Candidate(
                    id=thread.id,
                    mailbox=thread.mailbox,
                    subject=thread.subject,
                    category=thread.category,
                )
            ]

        stmt = (
            select(Thread)
            .where(Thread.id.in_(select(distinct(Draft.thread_id))))
            .order_by(Thread.last_message_at.desc().nullslast(), Thread.last_updated_at.desc())
        )
        if mailbox:
            stmt = stmt.where(Thread.mailbox == mailbox)
        elif allowed_mailboxes:
            stmt = stmt.where(Thread.mailbox.in_(allowed_mailboxes))
        if limit > 0:
            stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        threads = list(result.scalars().all())
        for thread in threads:
            session.expunge(thread)
        return [
            _Candidate(
                id=t.id,
                mailbox=t.mailbox,
                subject=t.subject,
                category=t.category,
            )
            for t in threads
        ]


async def _regenerate_one(
    candidate: _Candidate,
    *,
    instruction: str,
    apply: bool,
) -> str:
    if not apply:
        cat = candidate.category or "—"
        return f"dry_run:would_regenerate category={cat}"

    settings = get_settings()
    if not settings.mailbox_allowed(candidate.mailbox):
        return "skipped:mailbox_not_allowed"

    client = anthropic_client_from_settings(settings)
    openai_client = openai_client_from_settings(settings)
    factory = get_session_factory()

    try:
        async with factory() as session:
            persisted = await draft_regeneration_service.regenerate_draft(
                session,
                client=client,
                settings=settings,
                thread_id=candidate.id,
                instruction=instruction,
                actor="script",
                openai_client=openai_client,
                force_letter=True,
            )
    except ThreadNotFoundError as exc:
        return f"skipped:not_found:{exc}"
    except DraftGenerationError as exc:
        return f"failed:draft:{exc}"
    except Exception as exc:
        logger.exception("regenerate_drafts_failed", thread_id=str(candidate.id))
        return f"failed:{type(exc).__name__}:{exc}"

    skill_names = [s.name for s in (persisted.applied_skills or [])]
    skills_label = ",".join(skill_names) if skill_names else "(none)"
    actions = len(persisted.suggested_actions or [])
    return f"done:draft_id={persisted.id} skills=[{skills_label}] suggested_actions={actions}"


async def _run(
    *,
    apply: bool,
    mailbox: str | None,
    thread_id: uuid.UUID | None,
    limit: int,
    instruction: str,
    sleep_seconds: float,
) -> int:
    settings = get_settings()
    candidates = await _load_candidates(
        mailbox=mailbox,
        thread_id=thread_id,
        limit=limit,
        allowed_mailboxes=list(settings.mailbox_list),
    )
    scope = f"thread={thread_id}" if thread_id else (mailbox or "all allowed mailboxes")
    print(f"Found {len(candidates)} thread(s) with drafts ({scope}).")
    if not candidates:
        await dispose_engine()
        return 0

    results: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        outcome = await _regenerate_one(candidate, instruction=instruction, apply=apply)
        bucket = outcome.split(":", 1)[0]
        results[bucket] = results.get(bucket, 0) + 1
        print(f"  [{candidate.mailbox}] {candidate.subject!r} (id={candidate.id}) -> {outcome}")
        if apply and sleep_seconds > 0 and index < len(candidates) - 1:
            await asyncio.sleep(sleep_seconds)

    print("\nSummary:")
    for bucket, count in sorted(results.items()):
        print(f"  {bucket}: {count}")
    if not apply:
        print("\nDry run only — re-run with --apply to create new drafts.")
        print("Each apply call re-selects skills and regenerates suggested_actions + body.")

    await close_openai_client()
    await dispose_engine()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate drafts for threads that already have one "
            "(skills + suggested_actions + body)."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually call Sonnet and insert new draft rows. Default is dry run.",
    )
    parser.add_argument(
        "--mailbox",
        default=None,
        help="Only threads in this mailbox (default: all TARGET_MAILBOXES).",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Regenerate a single thread UUID (ignores --mailbox / --limit).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max threads to process when not using --thread-id (default: 20; 0 = all).",
    )
    parser.add_argument(
        "--instruction",
        default=_DEFAULT_INSTRUCTION,
        help="Reviewer instruction passed into draft regeneration.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=3.0,
        help="Seconds to wait between apply calls (default: 3).",
    )
    args = parser.parse_args(argv)

    thread_id: uuid.UUID | None = None
    if args.thread_id:
        try:
            thread_id = uuid.UUID(args.thread_id)
        except ValueError:
            print(f"Invalid --thread-id: {args.thread_id}", file=sys.stderr)
            return 1

    if args.limit < 0:
        print("--limit must be >= 0", file=sys.stderr)
        return 1

    try:
        return asyncio.run(
            _run(
                apply=args.apply,
                mailbox=args.mailbox,
                thread_id=thread_id,
                limit=args.limit,
                instruction=args.instruction,
                sleep_seconds=args.sleep,
            )
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
