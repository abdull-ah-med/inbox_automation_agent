"""Backfill alert fingerprints, associations, and optional recurrence on past threads.

Internal / Automated Now tags are derived at read time from sender + mailbox
domain (and automated locals). They do not need a DB backfill once that code
is deployed. This script refreshes what *is* persisted:

  1. alert_fingerprint / alert_signature / alert_sender_norm (automated alerts)
  2. thread_association_reviews via propose_alert_associations (fingerprint SQL)
  3. high-precision drip associations via propose_drip_associations (no OpenAI)
  4. optional hybrid related proposals (OpenAI) for cosine / near-subject links
  5. optional recurrence urgency floor on open automated clusters

Does not send or modify Outlook mail. Mail.Read only.

Usage (from backend/, with env loaded):

  # Dry run
  .venv/bin/python -m scripts.backfill_thread_signals --limit 50

  # Apply fingerprints + alert + drip associations for open Support threads
  .venv/bin/python -m scripts.backfill_thread_signals --apply \\
      --mailbox support@sample-site.example.com --open-only --limit 100

  # Skip drip associations
  .venv/bin/python -m scripts.backfill_thread_signals --apply --no-drip-assoc --limit 100

  # Also propose hybrid related links (needs OPENAI_API_KEY)
  .venv/bin/python -m scripts.backfill_thread_signals --apply --related-search --limit 30

  # Also apply recurrence urgency bumps for open automated clusters
  .venv/bin/python -m scripts.backfill_thread_signals --apply --recurrence --open-only

  # Single thread
  .venv/bin/python -m scripts.backfill_thread_signals --apply --thread-id <uuid>

EC2 (running compose):

  docker exec -i inbox-triage-automation-backend-1 \\
    python -m scripts.backfill_thread_signals --apply --open-only --limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from app.core.config import get_settings
from app.core.dependencies import close_openai_client, openai_client_from_settings
from app.core.tenant_scope import TenantScope
from app.db.session import dispose_engine, get_session_factory
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.repositories import message_repo, thread_repo
from app.services import recurrence_service, related_thread_service
from app.services.related_match import alert_cluster_keys

logger = structlog.get_logger(__name__)

_FINISHED = frozenset(
    {
        ThreadStateEnum.RESOLVED.value,
        ThreadStateEnum.NO_ACTION.value,
        ThreadStateEnum.SPAM.value,
    }
)


@dataclass(frozen=True)
class _Candidate:
    id: uuid.UUID
    mailbox: str
    subject: str
    state: str
    urgency: str | None
    has_fingerprint: bool


@dataclass
class _Outcome:
    fingerprint_set: bool = False
    alert_assoc: int = 0
    drip_assoc: int = 0
    related_assoc: int = 0
    recurrence: str | None = None
    skipped: str | None = None
    error: str | None = None


async def _load_candidates(
    *,
    mailbox: str | None,
    thread_id: uuid.UUID | None,
    limit: int,
    open_only: bool,
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
                    state=thread.state,
                    urgency=thread.urgency,
                    has_fingerprint=bool(thread.alert_fingerprint),
                )
            ]

        stmt = select(Thread).where(Thread.mailbox.in_(allowed_mailboxes))
        if mailbox:
            stmt = stmt.where(Thread.mailbox == mailbox.strip().lower())
        if open_only:
            stmt = stmt.where(Thread.state.notin_(sorted(_FINISHED)))
        stmt = stmt.order_by(Thread.last_message_at.desc().nullslast()).limit(limit)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        out: list[_Candidate] = []
        for thread in rows:
            session.expunge(thread)
            out.append(
                _Candidate(
                    id=thread.id,
                    mailbox=thread.mailbox,
                    subject=thread.subject,
                    state=thread.state,
                    urgency=thread.urgency,
                    has_fingerprint=bool(thread.alert_fingerprint),
                )
            )
        return out


async def _latest_inbound_sender(session, thread_id: uuid.UUID) -> str:
    rows = await message_repo.list_by_thread(session, thread_id)
    inbound = [row for row in rows if str(row.direction).lower() == "inbound"]
    if inbound:
        return inbound[-1].sender
    if rows:
        return rows[-1].sender
    return ""


async def _process_thread(
    candidate: _Candidate,
    *,
    apply: bool,
    do_fingerprint: bool,
    do_alert_assoc: bool,
    do_drip_assoc: bool,
    do_related_search: bool,
    do_recurrence: bool,
    openai_client,
) -> _Outcome:
    outcome = _Outcome()
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        thread = await thread_repo.get_by_id(
            session, candidate.id, TenantScope.from_settings(get_settings())
        )
        if thread is None:
            outcome.skipped = "missing"
            return outcome

        sender = await _latest_inbound_sender(session, thread.id)
        keys = alert_cluster_keys(
            mailbox=thread.mailbox,
            sender=sender,
            subject=thread.subject,
        )

        if do_fingerprint:
            if keys is None:
                if not thread.alert_fingerprint:
                    outcome.skipped = outcome.skipped or "not_alert_cluster"
            elif not thread.alert_fingerprint or thread.alert_fingerprint != keys[0]:
                outcome.fingerprint_set = True
                if apply:
                    fingerprint, signature, sender_norm = keys
                    await thread_repo.set_alert_fingerprint(
                        session,
                        thread.id,
                        fingerprint,
                        signature=signature,
                        sender_norm=sender_norm,
                    )

        if do_alert_assoc:
            if apply:
                proposed = await related_thread_service.propose_alert_associations(
                    session,
                    thread_id=thread.id,
                    now=datetime.now(UTC),
                )
                outcome.alert_assoc = len(proposed)
            else:
                # Dry run: only count if this thread can form an alert cluster.
                outcome.alert_assoc = 1 if (keys is not None or thread.alert_fingerprint) else 0

        if do_drip_assoc:
            if apply:
                proposed = await related_thread_service.propose_drip_associations(
                    session,
                    thread_id=thread.id,
                    now=datetime.now(UTC),
                )
                outcome.drip_assoc = len(proposed)
            else:
                outcome.drip_assoc = 1  # would attempt

        if do_related_search:
            if openai_client is None and apply:
                outcome.error = "related_search_needs_openai"
            elif apply:
                related = await related_thread_service.list_related(
                    session,
                    settings,
                    thread.id,
                    purpose="associated",
                    openai_client=openai_client,
                )
                outcome.related_assoc = len(related.items)
            else:
                outcome.related_assoc = 1  # would attempt

        if do_recurrence and thread.state not in _FINISHED:
            if apply:
                applied = await recurrence_service.apply_recurrence_escalation(
                    session,
                    thread_id=thread.id,
                    conversation_id=thread.conversation_id,
                    mailbox=thread.mailbox,
                    assessed_urgency=thread.urgency,
                    now=datetime.now(UTC),
                )
                outcome.recurrence = applied
            else:
                outcome.recurrence = "would_evaluate"

        if apply:
            await session.commit()
        else:
            await session.rollback()

    return outcome


async def _run(
    *,
    apply: bool,
    mailbox: str | None,
    thread_id: uuid.UUID | None,
    limit: int,
    open_only: bool,
    do_fingerprint: bool,
    do_alert_assoc: bool,
    do_drip_assoc: bool,
    do_related_search: bool,
    do_recurrence: bool,
) -> int:
    settings = get_settings()
    allowed = list(settings.mailbox_list)
    if not allowed:
        print("No TARGET_MAILBOXES configured.", file=sys.stderr)
        return 1

    candidates = await _load_candidates(
        mailbox=mailbox,
        thread_id=thread_id,
        limit=limit,
        open_only=open_only,
        allowed_mailboxes=allowed,
    )
    mode = "APPLY" if apply else "DRY RUN"
    print(f"{mode}: {len(candidates)} thread(s)")
    print(
        "Note: Internal / Automated Now tags are computed on read. "
        "No tag column to backfill."
    )
    if not candidates:
        await dispose_engine()
        return 0

    openai_client = None
    if do_related_search and apply:
        openai_client = openai_client_from_settings(settings)

    totals = {
        "fingerprint_set": 0,
        "alert_assoc_links": 0,
        "drip_assoc_links": 0,
        "related_assoc_links": 0,
        "recurrence_evaluated": 0,
        "errors": 0,
        "skipped": 0,
    }

    try:
        for candidate in candidates:
            try:
                outcome = await _process_thread(
                    candidate,
                    apply=apply,
                    do_fingerprint=do_fingerprint,
                    do_alert_assoc=do_alert_assoc,
                    do_drip_assoc=do_drip_assoc,
                    do_related_search=do_related_search,
                    do_recurrence=do_recurrence,
                    openai_client=openai_client,
                )
            except Exception as exc:  # noqa: BLE001 — CLI surface per thread
                totals["errors"] += 1
                print(
                    f"  ERROR [{candidate.mailbox}] {candidate.subject!r} "
                    f"id={candidate.id} -> {exc}"
                )
                logger.exception("backfill_thread_failed", thread_id=str(candidate.id))
                continue

            if outcome.error:
                totals["errors"] += 1
                print(
                    f"  ERROR [{candidate.mailbox}] {candidate.subject!r} "
                    f"id={candidate.id} -> {outcome.error}"
                )
                continue

            if outcome.fingerprint_set:
                totals["fingerprint_set"] += 1
            totals["alert_assoc_links"] += outcome.alert_assoc
            totals["drip_assoc_links"] += outcome.drip_assoc
            totals["related_assoc_links"] += outcome.related_assoc
            if outcome.recurrence is not None:
                totals["recurrence_evaluated"] += 1
            if outcome.skipped:
                totals["skipped"] += 1

            bits = [
                (
                    "fp="
                    + (
                        "set"
                        if outcome.fingerprint_set
                        else ("had" if candidate.has_fingerprint else "none")
                    )
                ),
                f"alert_assoc={outcome.alert_assoc}",
            ]
            if do_drip_assoc:
                bits.append(f"drip_assoc={outcome.drip_assoc}")
            if do_related_search:
                bits.append(f"related={outcome.related_assoc}")
            if do_recurrence:
                bits.append(f"recurrence={outcome.recurrence}")
            if outcome.skipped:
                bits.append(f"skip={outcome.skipped}")
            print(
                f"  [{candidate.state}/{candidate.urgency or '-'}] "
                f"[{candidate.mailbox}] {candidate.subject!r} "
                f"id={candidate.id} -> {', '.join(bits)}"
            )
    finally:
        if openai_client is not None:
            await close_openai_client()
        await dispose_engine()

    print("\nSummary:")
    for key, value in totals.items():
        print(f"  {key}: {value}")
    if not apply:
        print("\nDry run only. Re-run with --apply to write fingerprints / associations.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write fingerprints, associations, and optional recurrence. Default is dry run.",
    )
    parser.add_argument("--mailbox", default=None, help="Limit to one mailbox address.")
    parser.add_argument("--thread-id", default=None, help="Single thread UUID.")
    parser.add_argument("--limit", type=int, default=100, help="Max threads (default 100).")
    parser.add_argument(
        "--open-only",
        action="store_true",
        help="Skip RESOLVED / NO_ACTION / SPAM.",
    )
    parser.add_argument(
        "--no-fingerprint",
        action="store_true",
        help="Skip alert fingerprint backfill.",
    )
    parser.add_argument(
        "--no-alert-assoc",
        action="store_true",
        help="Skip propose_alert_associations.",
    )
    parser.add_argument(
        "--no-drip-assoc",
        action="store_true",
        help="Skip propose_drip_associations (same sender + base subject).",
    )
    parser.add_argument(
        "--related-search",
        action="store_true",
        help="Also run hybrid related-thread proposals (needs OPENAI_API_KEY).",
    )
    parser.add_argument(
        "--recurrence",
        action="store_true",
        help="Also apply recurrence urgency escalation on open threads.",
    )
    args = parser.parse_args(argv)

    thread_id = None
    if args.thread_id:
        try:
            thread_id = uuid.UUID(args.thread_id)
        except ValueError:
            print(f"Invalid --thread-id: {args.thread_id}", file=sys.stderr)
            return 2

    try:
        return asyncio.run(
            _run(
                apply=args.apply,
                mailbox=args.mailbox,
                thread_id=thread_id,
                limit=max(1, args.limit),
                open_only=args.open_only,
                do_fingerprint=not args.no_fingerprint,
                do_alert_assoc=not args.no_alert_assoc,
                do_drip_assoc=not args.no_drip_assoc,
                do_related_search=args.related_search,
                do_recurrence=args.recurrence,
            )
        )
    except Exception as exc:  # noqa: BLE001 — CLI surface
        print(f"Fatal: {exc}", file=sys.stderr)
        logger.exception("backfill_thread_signals_fatal")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
