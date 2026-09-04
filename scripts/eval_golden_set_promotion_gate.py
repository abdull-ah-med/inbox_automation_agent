#!/usr/bin/env python
"""Live promotion-gate checks against the imported high-confidence golden set."""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


async def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / "backend" / ".env")

    from app.db.session import get_session_factory
    from app.repositories.promotion_proposal_repo import PromotionProposalSchema
    from app.services.promotion_gate import evaluate_against_golden_set

    expires = datetime.now(UTC) + timedelta(days=30)
    mailbox = "info@sample-services.example.com"
    factory = get_session_factory()
    results: dict[str, bool] = {}

    async with factory() as session:
        note = PromotionProposalSchema(
            id=uuid.uuid4(),
            mailbox=mailbox,
            kind="teaching_note",
            payload={"requested_scope": "mailbox"},
            impact_num=1,
            impact_den=1,
            evidence_ids=[],
            status="pending",
            expires_at=expires,
            created_at=datetime.now(UTC),
        )
        results["teaching_note"] = await evaluate_against_golden_set(session, note)

        unmatched = PromotionProposalSchema(
            id=uuid.uuid4(),
            mailbox=mailbox,
            kind="urgency_rule",
            payload={
                "condition": {"sender_domain": "example-unmatched.test"},
                "action": {"set_urgency_floor": "HIGH"},
                "requested_scope": "mailbox",
            },
            impact_num=1,
            impact_den=1,
            evidence_ids=[],
            status="pending",
            expires_at=expires,
            created_at=datetime.now(UTC),
        )
        results["urgency_unmatched_domain"] = await evaluate_against_golden_set(
            session, unmatched
        )

    print(json_dumps(results))
    # teaching notes fail closed while draft criteria exist and no LLM judge in gate
    if results["teaching_note"] is not False:
        print("ERROR: teaching_note should fail closed on this set", file=sys.stderr)
        return 1
    if results["urgency_unmatched_domain"] is not True:
        print("ERROR: unmatched urgency rule should pass (no expected_urgency)", file=sys.stderr)
        return 1
    print("PASS  promotion gate: teaching_note fail-closed; unmatched urgency_rule pass")
    return 0


def json_dumps(obj: dict) -> str:
    import json

    return json.dumps(obj)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
