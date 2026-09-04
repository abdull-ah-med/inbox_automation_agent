#!/usr/bin/env python
"""Evaluate the frozen high-confidence golden set (local, no prod writes).

Proves two things without using the model as its own oracle:

1. Inventory — 20 human-verified cases, 0 invented urgencies, 8 no-reply, 12 replies.
2. Preference-pair oracles — DraftAssistant's rejected draft violates a hand-written constraint;
   Elise's send does not. Literals from reading the mail, not from production code.

Usage (repo root):
  python scripts/eval_high_confidence_golden_set.py
  python scripts/eval_high_confidence_golden_set.py --db   # also ping Postgres rows
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "misc/data/golden-set-high-confidence.json"

# Hand-checked: if someone swapped gold and DraftAssistant, these fail.
# Each tuple is (candidate_id, draftassistant_must_contain, gold_must_contain, gold_must_not_contain).
PAIR_ORACLES: list[tuple[str, str, str, str | None]] = [
    (
        "06c5ec5dd344",
        "happy to assist with a background check",
        "we do not place background check orders",
        "date of birth",
    ),
    (
        "25ec27a8a913",
        "hi smit",
        "thanks div",
        "dev team",
    ),
    (
        "e0e765f1106a",
        "approve the fee",
        "felony only",
        "approve the fee",
    ),
    (
        "60efb61af449",
        "hi norma",
        "sterling transportation",
        "ritaa@",
    ),
    (
        "28c4dc45224a",
        "has the background check authorization",
        "mr. neal submitted a dispute",
        "has the background check authorization",
    ),
    (
        "320a7ebdec00",
        "samplecontact",
        "virustotal",
        "samplecontact",
    ),
]


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    raise SystemExit(1)


def eval_file(payload: dict) -> None:
    cases = payload["cases"]
    if len(cases) != 20:
        _fail(f"expected 20 kept cases, got {len(cases)}")

    urgencies = [c.get("expected_urgency") for c in cases]
    if any(u is not None for u in urgencies):
        _fail("high-confidence set must not invent expected_urgency (all must be null)")

    no_action = [c for c in cases if c["expected_action"] == "no_action_discarded"]
    action = [c for c in cases if c["expected_action"] == "action_needed"]
    if len(no_action) != 8:
        _fail(f"expected 8 no_action_discarded, got {len(no_action)}")
    if len(action) != 12:
        _fail(f"expected 12 action_needed, got {len(action)}")

    for c in no_action:
        if c.get("gold_reply"):
            _fail(f"{c['candidate_id']}: no-reply case must not carry a gold reply")
        if c.get("expected_draft_body_criteria"):
            _fail(f"{c['candidate_id']}: no-reply case must not have draft criteria")

    for c in action:
        if not (c.get("gold_reply") or "").strip():
            _fail(f"{c['candidate_id']}: action_needed case missing gold_reply")
        if not (c.get("expected_draft_body_criteria") or "").strip():
            _fail(f"{c['candidate_id']}: action_needed case missing draft criteria")

    by_id = {c["candidate_id"]: c for c in cases}
    for cid, draftassistant_need, gold_need, gold_forbid in PAIR_ORACLES:
        case = by_id.get(cid)
        if case is None:
            _fail(f"pair oracle {cid} missing from kept set")
        draftassistant = (case.get("draftassistant_rejected_draft") or "").lower()
        gold = (case.get("gold_reply") or "").lower()
        if draftassistant_need.lower() not in draftassistant:
            _fail(f"{cid}: DraftAssistant draft should contain {draftassistant_need!r} (the bad behavior)")
        if gold_need.lower() not in gold:
            _fail(f"{cid}: human send should contain {gold_need!r}")
        if gold_forbid and gold_forbid.lower() in gold:
            _fail(f"{cid}: human send must not contain {gold_forbid!r}")
        print(f"PASS  pair {cid}: DraftAssistant has bad pattern; send matches human constraint")

    print(f"PASS  inventory: {len(cases)} cases, 0 invented urgencies, {len(no_action)} no-reply, {len(action)} reply")


async def eval_db() -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    from sqlalchemy import func, select

    from app.db.session import get_session_factory
    from app.models.db.golden_set_case import GoldenSetCase

    factory = get_session_factory()
    async with factory() as session:
        n = (await session.execute(select(func.count()).select_from(GoldenSetCase))).scalar_one()
        urg = (
            await session.execute(
                select(func.count())
                .select_from(GoldenSetCase)
                .where(GoldenSetCase.expected_urgency.is_not(None))
            )
        ).scalar_one()
        actions = (
            await session.execute(
                select(GoldenSetCase.expected_action, func.count())
                .group_by(GoldenSetCase.expected_action)
            )
        ).all()
    print(f"DB    golden_set_cases rows={n}  with_urgency={urg}  by_action={dict(actions)}")
    if n < 20:
        _fail(f"DB has {n} golden rows; import the high-confidence set (--apply)")
    if urg:
        _fail(f"DB has {urg} rows with expected_urgency; high-confidence import should leave these null")
    print("PASS  database inventory")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="store_true", help="Also check rows in local Postgres")
    args = parser.parse_args()

    if not CASES_PATH.exists():
        _fail(f"missing {CASES_PATH}")
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    eval_file(payload)
    if args.db:
        asyncio.run(eval_db())
    print("\nAll high-confidence golden-set checks passed.")


if __name__ == "__main__":
    main()
