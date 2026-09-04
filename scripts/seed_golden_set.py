#!/usr/bin/env python
"""Seed a small handful of placeholder golden-set cases per mailbox.

Usage:
  python scripts/seed_golden_set.py            # dry-run (default)
  python scripts/seed_golden_set.py --apply    # writes to database

Each case represents a canonical triage scenario.  These are placeholders —
the operations team should replace email_text with real anonymised excerpts
and tune expected_urgency per their domain knowledge.

Deliberately small (3-5 per mailbox): the spec calls for 30-100 hand-curated
cases per mailbox, but the initial PR only ships the table + seed;
humans complete curation offline.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

PLACEHOLDER_CASES: list[dict] = [
    {
        "email_text": (
            "Subject: DOT audit response due in 24 hours\n\n"
            "This is a reminder that your DOT audit response is due tomorrow. "
            "Please submit the required documentation immediately."
        ),
        "expected_urgency": "CRITICAL",
        "expected_action": "action_needed",
    },
    {
        "email_text": (
            "Subject: Drug screen result — positive\n\n"
            "The driver's drug screen came back positive. "
            "Please advise on next steps per your policy."
        ),
        "expected_urgency": "HIGH",
        "expected_action": "action_needed",
    },
    {
        "email_text": (
            "Subject: Request for onboarding packet\n\n"
            "Hi, could you send the onboarding packet for our new hire? "
            "No rush — whenever you get a chance."
        ),
        "expected_urgency": "NORMAL",
        "expected_action": "action_needed",
    },
    {
        "email_text": (
            "Subject: Automated confirmation: payment received\n\n"
            "This is an automated confirmation that your payment has been received. "
            "No action is required."
        ),
        "expected_urgency": "LOW",
        "expected_action": "no_action_discarded",
    },
    {
        "email_text": (
            "Subject: Compliance newsletter — July 2026\n\n"
            "This month's transportation compliance tips: ensure all drivers "
            "complete their annual training. See attached PDF."
        ),
        "expected_urgency": "LOW",
        "expected_action": "no_action_discarded",
    },
]


async def _seed(mailboxes: list[str], dry_run: bool) -> None:
    from app.core.config import get_settings
    from app.db.session import get_session_factory
    from app.repositories.golden_set_repo import insert_golden_case

    settings = get_settings()
    if not mailboxes:
        mailboxes = settings.mailbox_list

    if not mailboxes:
        print("ERROR: no mailboxes configured (TARGET_MAILBOXES is empty).", file=sys.stderr)
        sys.exit(1)

    total = 0
    factory = get_session_factory()

    for mailbox in mailboxes:
        print(f"\nMailbox: {mailbox}")
        for case in PLACEHOLDER_CASES:
            desc = case["email_text"].split("\n")[0][:60]
            if dry_run:
                print(f"  [dry-run] would insert: {desc!r} → {case['expected_urgency']}")
            else:
                async with factory() as session:
                    row = await insert_golden_case(
                        session,
                        mailbox=mailbox,
                        email_text=case["email_text"],
                        expected_urgency=case.get("expected_urgency"),
                        expected_action=case.get("expected_action"),
                    )
                    await session.commit()
                    print(f"  inserted {row.id}: {desc!r} → {case['expected_urgency']}")
            total += 1

    print(f"\n{'[dry-run] ' if dry_run else ''}Total: {total} case(s) across {len(mailboxes)} mailbox(es).")
    if dry_run:
        print("Re-run with --apply to write to the database.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually write to the database (default: dry-run only)",
    )
    parser.add_argument(
        "--mailbox",
        action="append",
        dest="mailboxes",
        default=[],
        help="Override mailbox(es) to seed (repeat for multiple; default: TARGET_MAILBOXES)",
    )
    args = parser.parse_args()

    asyncio.run(_seed(args.mailboxes, dry_run=not args.apply))


if __name__ == "__main__":
    main()
