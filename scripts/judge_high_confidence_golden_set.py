#!/usr/bin/env python
"""LLM-as-judge on the high-confidence golden set (OpenAI, binary pass/fail).

Splits each case into REQUIRED (gold must satisfy) vs FORBIDDEN (DraftAssistant must not).
Gold is graded as a human send: names/IDs in the send are allowed unless they
match FORBIDDEN. DraftAssistant fails on any FORBIDDEN item.

Usage (repo root, needs OPENAI_API_KEY in backend/.env):
  backend/.venv/bin/python scripts/judge_high_confidence_golden_set.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

CASES_PATH = ROOT / "misc/data/golden-set-high-confidence.json"
OUT_PATH = ROOT / "misc/data/golden-set-judge-results.json"

# Independent of production code. Derived by reading each send vs DraftAssistant draft.
JUDGE_RUBRICS: dict[str, dict[str, str]] = {
    "30a940ff29a0": {
        "required": (
            "Mentions Equifax and a holding pattern, and that the inquiring party "
            "is not urgently needing this. Saying they will share timing later is "
            "a pass."
        ),
        "forbidden": (
            "A concrete calendar date (month/day/year) for go-live. Do not fail "
            "for saying timing will come after Equifax setup."
        ),
    },
    "06c5ec5dd344": {
        "required": (
            "States they do not place individual background-check orders and that "
            "they administer reports for employers. Directs the person to contact "
            "SAMPLERECORDS or the employer."
        ),
        "forbidden": (
            "Offering to run a background check. Asking for date of birth or SSN "
            "to start an order."
        ),
    },
    "25ec27a8a913": {
        "required": (
            "Addresses Div. Explains that programmatic terms/discount on the "
            "QBO-tied field should stop the invoice sync mismatch."
        ),
        "forbidden": (
            "Greeting 'Smit'. Saying 'our dev team' did the work. Promising to "
            "monitor for days or offering complimentary Professional Services."
        ),
    },
    "320a7ebdec00": {
        "required": (
            "VirusTotal and/or SSL are clean. Next step is safelisting (Brian / "
            "IT / the affected user submitting to the provider)."
        ),
        "forbidden": (
            "Greeting 'Samplecontact'. Claiming an alphaMountain.ai false-positive "
            "ticket was already submitted."
        ),
    },
    "28c4dc45224a": {
        "required": (
            "States the dispute status with a report id and/or case id. Attaching "
            "or offering FCRA/consumer-rights copies is a pass."
        ),
        "forbidden": (
            "Asking whether consent/authorization was submitted. Asking whether "
            "to initiate a new background-check order."
        ),
    },
    "e0e765f1106a": {
        "required": (
            "Accepts felony-only NJ county searches if the report is clearly "
            "labeled felony only."
        ),
        "forbidden": "The words 'approve the fee' or equivalent fee-approval instruction.",
    },
    "60efb61af449": {
        "required": (
            "Says an account detail for Sterling Transportation is attached. A "
            "short send with no extra contacts is a pass."
        ),
        "forbidden": (
            "Greeting 'Norma'. Naming Rita Arizmendi. Inventing emails such as "
            "ritaa@ or corporateap@."
        ),
    },
    "3b09be00839f": {
        "required": (
            "About the driver-verification list / export button. Asking to put "
            "the export control on that screen is a pass."
        ),
        "forbidden": "Invoices, billing rates, or a Samba/CDLIS commitment.",
    },
    "ad1243a46977": {
        "required": (
            "Manual entry is acceptable for now. A conditional 'if Samba/API is "
            "ready, then a DD ticket' is a pass."
        ),
        "forbidden": (
            "Saying they cannot proceed until Samba integration is done. Treating "
            "manual entry as unacceptable."
        ),
    },
    "10f6de0f9e1a": {
        "required": (
            "Asks that externaldonorID appear on charges across account and "
            "subaccount. Offering a draft account structure later is a pass."
        ),
        "forbidden": (
            "Asserting as fact that subaccounts do or do not have their own "
            "clinic-network rules."
        ),
    },
    "a7bbadfd9497": {
        "required": (
            "SAMPLERECORDS/PR stays without state-of-employment. Clear Connect will be "
            "configured with it. Offering to add it to SAMPLERECORDS/PR later if a window "
            "opens is a pass."
        ),
        "forbidden": (
            "Saying the existing SAMPLERECORDS/PR account will now require state of "
            "employment."
        ),
    },
    "60f6df76cb58": {
        "required": (
            "Acknowledges the new driver will be loaded and/or that Edwin was "
            "removed. Explaining CDL expiry for the original driver is a pass. "
            "Mentioning existing portal/invoicing access for a named coworker is "
            "a pass."
        ),
        "forbidden": (
            "Inventing a new password or a new login URL that is not a portal "
            "name. Demanding payment or a new invoice."
        ),
    },
}

GOLD_SYSTEM = """You are grading a human-sent shared-inbox reply against a binary rubric.
Return ONLY valid JSON: {"pass": true or false, "reason": "one or two sentences"}.
This draft IS the human send. Names, IDs, dates, and operational facts that
appear in the draft are allowed unless they match FORBIDDEN.
pass=true only if EVERY REQUIRED item is satisfied AND none of FORBIDDEN occurs.
Greetings and signatures do not decide the grade.
"""

DRAFTASSISTANT_SYSTEM = """You are grading a rejected model draft against a binary rubric.
Return ONLY valid JSON: {"pass": true or false, "reason": "one or two sentences"}.
pass=false if ANY FORBIDDEN item occurs, even if some REQUIRED substance is present.
pass=true only if no FORBIDDEN item occurs AND REQUIRED substance is present.
Greetings and signatures do not decide the grade.
"""


async def _grade(
    client,
    *,
    model: str,
    system: str,
    inbound: str,
    draft: str,
    required: str,
    forbidden: str,
) -> dict:
    user = (
        f"REQUIRED (all must be true):\n{required}\n\n"
        f"FORBIDDEN (any of these is a fail):\n{forbidden}\n\n"
        f"INBOUND EMAIL (background only; not a complete fact list):\n{inbound}\n\n"
        f"DRAFT TO GRADE:\n{draft}\n"
    )
    resp = await client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    parsed = json.loads(raw)
    return {
        "pass": bool(parsed.get("pass")),
        "reason": str(parsed.get("reason") or ""),
        "model": model,
        "usage": {
            "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
            "completion_tokens": getattr(resp.usage, "completion_tokens", None),
        },
    }


async def main() -> int:
    from dotenv import load_dotenv
    from openai import AsyncOpenAI

    from app.core.config import get_settings

    load_dotenv(ROOT / "backend" / ".env")
    settings = get_settings()
    if not settings.openai_api_key.strip():
        print("ERROR: OPENAI_API_KEY is not set", file=sys.stderr)
        return 1

    model = os.environ.get("EVAL_JUDGE_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = [c for c in payload["cases"] if (c.get("expected_draft_body_criteria") or "").strip()]
    missing = [c["candidate_id"] for c in cases if c["candidate_id"] not in JUDGE_RUBRICS]
    if missing:
        print(f"ERROR: no split rubric for {missing}", file=sys.stderr)
        return 1

    client = AsyncOpenAI(api_key=settings.openai_api_key.strip())
    rows: list[dict] = []
    gold_pass = 0
    draftassistant_fail = 0
    draftassistant_n = 0

    try:
        for case in cases:
            cid = case["candidate_id"]
            rubric = JUDGE_RUBRICS[cid]
            inbound = case["email_text"]
            gold = case.get("gold_reply") or ""
            draftassistant = case.get("draftassistant_rejected_draft") or ""

            gold_grade = await _grade(
                client,
                model=model,
                system=GOLD_SYSTEM,
                inbound=inbound,
                draft=gold,
                required=rubric["required"],
                forbidden=rubric["forbidden"],
            )
            gold_pass += int(gold_grade["pass"])
            mark = "PASS" if gold_grade["pass"] else "FAIL"
            print(f"{mark}  gold  {cid}  {case['mailbox'].split('@')[0]}")
            print(f"      {gold_grade['reason'][:220]}")

            draftassistant_grade = None
            if draftassistant.strip():
                draftassistant_n += 1
                draftassistant_grade = await _grade(
                    client,
                    model=model,
                    system=DRAFTASSISTANT_SYSTEM,
                    inbound=inbound,
                    draft=draftassistant,
                    required=rubric["required"],
                    forbidden=rubric["forbidden"],
                )
                draftassistant_fail += int(not draftassistant_grade["pass"])
                mark_i = "FAIL(good)" if not draftassistant_grade["pass"] else "PASS(bad)"
                print(f"{mark_i} draftassistant  {cid}")
                print(f"      {draftassistant_grade['reason'][:220]}")

            rows.append(
                {
                    "candidate_id": cid,
                    "mailbox": case["mailbox"],
                    "source_bucket": case["source_bucket"],
                    "required": rubric["required"],
                    "forbidden": rubric["forbidden"],
                    "gold": gold_grade,
                    "draftassistant": draftassistant_grade,
                }
            )
    finally:
        await client.close()

    summary = {
        "gold_n": len(cases),
        "gold_pass": gold_pass,
        "gold_pass_rate": round(gold_pass / len(cases), 3) if cases else None,
        "draftassistant_n": draftassistant_n,
        "draftassistant_fail": draftassistant_fail,
        "draftassistant_fail_rate": round(draftassistant_fail / draftassistant_n, 3) if draftassistant_n else None,
        "expected": "gold mostly pass; draftassistant mostly fail",
    }
    out = {
        "kind": "golden_set_llm_judge",
        "rubric": "required_vs_forbidden",
        "created_at": datetime.now(UTC).isoformat(),
        "model": model,
        "summary": summary,
        "rows": rows,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + json.dumps(summary, indent=2))
    print(f"wrote {OUT_PATH}")

    ok = True
    if gold_pass < (len(cases) / 2):
        print("ERROR: fewer than half of human sends passed the judge", file=sys.stderr)
        ok = False
    if draftassistant_n and draftassistant_fail < (draftassistant_n / 2):
        print("ERROR: fewer than half of DraftAssistant rejected drafts failed the judge", file=sys.stderr)
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
