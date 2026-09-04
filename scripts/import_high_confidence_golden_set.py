#!/usr/bin/env python
"""Load the high-confidence golden set into local Postgres. Does not use placeholders.

Usage (from repo root):
  python scripts/import_high_confidence_golden_set.py            # dry-run
  python scripts/import_high_confidence_golden_set.py --apply    # insert

Idempotent: skips a row when the same mailbox + email_text already exists.
Never seeds the invented PLACEHOLDER_CASES from seed_golden_set.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

CASES_PATH = ROOT / "misc/data/golden-set-high-confidence.json"


async def _existing_keys(session) -> set[tuple[str, str]]:
    from sqlalchemy import select

    from app.models.db.golden_set_case import GoldenSetCase

    rows = (
        await session.execute(select(GoldenSetCase.mailbox, GoldenSetCase.email_text))
    ).all()
    return {(r[0], r[1]) for r in rows}


async def _run(*, apply: bool) -> None:
    from app.db.session import get_session_factory
    from app.repositories.golden_set_repo import insert_golden_case

    if not CASES_PATH.exists():
        print(f"ERROR: missing {CASES_PATH}", file=sys.stderr)
        sys.exit(1)

    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"]
    print(f"Source: {CASES_PATH.name}  kept={len(cases)}  policy={payload.get('policy','')[:80]}…")

    factory = get_session_factory()
    inserted = 0
    skipped = 0
    async with factory() as session:
        existing = await _existing_keys(session)
        for case in cases:
            key = (case["mailbox"], case["email_text"])
            desc = (case["email_text"].split("\n", 1)[0])[:70]
            if key in existing:
                print(f"  skip  {case['candidate_id']}  {case['expected_action']:22}  {desc}")
                skipped += 1
                continue
            print(
                f"  {'insert' if apply else 'dry  '}  {case['candidate_id']}  "
                f"{case['expected_action']:22}  urg={case['expected_urgency']!r}  {desc}"
            )
            if apply:
                await insert_golden_case(
                    session,
                    mailbox=case["mailbox"],
                    email_text=case["email_text"],
                    sender_domain=case.get("sender_domain"),
                    expected_urgency=case.get("expected_urgency"),
                    expected_action=case.get("expected_action"),
                    expected_associations=case.get("expected_associations"),
                    expected_draft_body_criteria=case.get("expected_draft_body_criteria"),
                )
                existing.add(key)
                inserted += 1
        if apply:
            await session.commit()

    print(f"\n{'Applied' if apply else 'Dry-run'}: insert={inserted} skip={skipped} total={len(cases)}")
    if not apply:
        print("Re-run with --apply to write to the database.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(apply=args.apply))


if __name__ == "__main__":
    main()
