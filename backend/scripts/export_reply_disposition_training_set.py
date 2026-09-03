"""Export a reply-disposition training set from local Postgres threads.

Usage:
  .venv/bin/python -m scripts.export_reply_disposition_training_set
  .venv/bin/python -m scripts.export_reply_disposition_training_set --out ../data/reply_disposition_training_v1.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.closing_mail import looks_like_closing_mail
from app.core.config import get_settings

BEHAVIOR_SPEC = {
    "resolved_no_reply": (
        "No reply needed. Thread should be RESOLVED with auto-resolved banner "
        "(reason shown) and feedback loop. Includes CC observer, courtesy close, "
        "plain thanks."
    ),
    "open_no_reply_fyi": (
        "No reply needed but thread stays OPEN. Show brief reason in draft "
        "section (teaching note / briefing), not resolved."
    ),
    "action_needed": (
        "Reply or action needed. Stay in Needs Attention until Elise approves, "
        "rejects, or manually resolves."
    ),
    "resolved_manual": (
        "Elise explicitly marked resolved (with actions-taken capture). "
        "Resolution banner + reopen feedback."
    ),
    "uncertain": "Needs human label — ambiguous or conflicting signals.",
}


def norm_email(addr: str) -> str:
    return (addr or "").strip().lower()


def mailbox_in_to(mailbox: str, tos: list[str] | None) -> bool:
    m = norm_email(mailbox)
    return any(norm_email(t) == m for t in (tos or []))


def mailbox_in_cc(mailbox: str, ccs: list[str] | None) -> bool:
    m = norm_email(mailbox)
    return any(norm_email(c) == m for c in (ccs or []))


def mentions_poi(body: str, mailbox: str) -> bool:
    text = (body or "").lower()
    if "elise" in text:
        return True
    local = mailbox.split("@")[0].lower() if "@" in mailbox else ""
    return bool(local and local in text)


def snippet(s: str, n: int = 320) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s[:n] + ("…" if len(s) > n else "")


def expected_behavior(label: str) -> dict[str, object]:
    if label == "resolved_no_reply":
        return {
            "ui_surface": "resolution_banner",
            "thread_state": "RESOLVED",
            "draft_needed": False,
            "has_action_items": False,
            "show_reason_in_draft_section": False,
            "elise_actions": ["reopen", "wrong_reason_feedback"],
        }
    if label == "open_no_reply_fyi":
        return {
            "ui_surface": "draft_section_briefing",
            "thread_state": "DRAFTED",
            "draft_needed": False,
            "has_action_items": True,
            "show_reason_in_draft_section": True,
            "elise_actions": ["generate_reply", "mark_resolved", "reject_no_reply"],
        }
    if label == "action_needed":
        return {
            "ui_surface": "draft_section",
            "thread_state": "DRAFTED",
            "draft_needed": True,
            "has_action_items": True,
            "show_reason_in_draft_section": False,
            "elise_actions": ["approve", "reject", "mark_resolved"],
        }
    if label == "resolved_manual":
        return {
            "ui_surface": "resolution_banner",
            "thread_state": "RESOLVED",
            "draft_needed": False,
            "has_action_items": False,
            "show_reason_in_draft_section": False,
            "elise_actions": ["reopen"],
        }
    return {
        "ui_surface": "needs_review",
        "thread_state": None,
        "draft_needed": None,
        "has_action_items": None,
        "show_reason_in_draft_section": None,
        "elise_actions": [],
    }


def suggest_label(
    *,
    state: str,
    body: str,
    mailbox: str,
    tos: list[str],
    ccs: list[str],
    draft: dict | None,
) -> tuple[str, str]:
    cc_only = mailbox_in_cc(mailbox, ccs) and not mailbox_in_to(mailbox, tos)
    closing = looks_like_closing_mail(body)
    addressed = mentions_poi(body, mailbox)

    if draft and draft.get("feedback_action") == "wrong":
        code = draft.get("feedback_reason_code") or ""
        if code in {"no_reply_needed", "wrong_action"}:
            return "resolved_no_reply", f"Reviewer marked no reply: {code}"

    if state == "RESOLVED":
        return "resolved_manual", "Thread RESOLVED in DB"

    if state == "NO_ACTION":
        if cc_only and not addressed:
            return "resolved_no_reply", "CC-only observer; PoI not addressed"
        if closing:
            return "resolved_no_reply", "NO_ACTION + courtesy-close body"
        return "resolved_no_reply", "NO_ACTION triage discard"

    if closing:
        if addressed and not cc_only:
            return "resolved_no_reply", "Courtesy close / thanks to PoI"
        return "resolved_no_reply", "Courtesy close or CC observer"

    if state == "DRAFTED" and draft:
        teaching = (draft.get("teaching_note") or "").lower()
        body_l = body.lower()
        fyi_markers = (
            "updated",
            "completed",
            "screens",
            "fyi",
            "for your records",
            "heads up",
            "status update",
            "on our side",
        )
        if any(m in teaching or m in body_l for m in fyi_markers) and not closing:
            return "open_no_reply_fyi", "Operational FYI — acknowledge but stay open"

    if state in {"DRAFTED", "REQUIRES_HUMAN", "NEW"} and (mailbox_in_to(mailbox, tos) or addressed):
        return "action_needed", "Direct ask or PoI in To / addressed"

    return "uncertain", "Conflicting or weak signals"


async def export_training_set(out_path: Path) -> dict:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        """
            WITH latest_inbound AS (
              SELECT DISTINCT ON (m.thread_id)
                m.thread_id,
                m.sender,
                m.body_text,
                m.unique_body_text,
                m.to_recipients,
                m.cc_recipients,
                m.received_at
              FROM messages m
              WHERE m.direction = 'inbound'
              ORDER BY m.thread_id, m.received_at DESC
            )
            SELECT
              t.id::text AS thread_id,
              t.conversation_id,
              t.mailbox,
              t.subject,
              t.state,
              t.category,
              t.urgency,
              li.sender,
              li.body_text,
              li.unique_body_text,
              li.to_recipients,
              li.cc_recipients,
              li.received_at
            FROM threads t
            JOIN latest_inbound li ON li.thread_id = t.id
            ORDER BY li.received_at DESC
            """
                    )
                )
            )
            .mappings()
            .all()
        )

        drafts = (
            (
                await conn.execute(
                    text(
                        """
            SELECT DISTINCT ON (d.thread_id)
              d.thread_id::text,
              d.feedback_action,
              d.feedback_note,
              d.feedback_reason_code,
              d.teaching_note,
              d.body,
              d.approved_at,
              d.rejected_at
            FROM drafts d
            ORDER BY d.thread_id, d.created_at DESC
            """
                    )
                )
            )
            .mappings()
            .all()
        )

        audit_counts = (
            await conn.execute(
                text(
                    """
            SELECT event_type, COUNT(*) AS n
            FROM audit_events
            GROUP BY event_type
            ORDER BY n DESC
            """
                )
            )
        ).fetchall()

    await engine.dispose()

    draft_by_thread = {d["thread_id"]: dict(d) for d in drafts}
    buckets: dict[str, list[dict]] = defaultdict(list)

    for r in rows:
        tid = r["thread_id"]
        mailbox = r["mailbox"]
        body = r["unique_body_text"] or r["body_text"] or ""
        tos = list(r["to_recipients"] or [])
        ccs = list(r["cc_recipients"] or [])
        draft = draft_by_thread.get(tid)
        label, reason = suggest_label(
            state=r["state"],
            body=body,
            mailbox=mailbox,
            tos=tos,
            ccs=ccs,
            draft=draft,
        )
        ex = {
            "id": f"db-{tid[:8]}",
            "thread_id": tid,
            "conversation_id": r["conversation_id"],
            "mailbox": mailbox,
            "subject": r["subject"],
            "current_db_state": r["state"],
            "category": r["category"],
            "urgency": r["urgency"],
            "sender": r["sender"],
            "received_at": r["received_at"].isoformat() if r["received_at"] else None,
            "to_recipients": tos,
            "cc_recipients": ccs,
            "body": body[:4000],
            "body_snippet": snippet(body),
            "suggested_label": label,
            "suggestion_reason": reason,
            "label_confidence": "high" if label != "uncertain" else "low",
            "expected_behavior": expected_behavior(label),
            "reviewer_label": None,
            "reviewer_notes": None,
            "signals": {
                "cc_only": mailbox_in_cc(mailbox, ccs) and not mailbox_in_to(mailbox, tos),
                "poi_in_to": mailbox_in_to(mailbox, tos),
                "poi_in_cc": mailbox_in_cc(mailbox, ccs),
                "mentions_poi": mentions_poi(body, mailbox),
                "closing_heuristic": looks_like_closing_mail(body),
                "draft_feedback_action": (draft or {}).get("feedback_action"),
                "draft_feedback_reason_code": (draft or {}).get("feedback_reason_code"),
                "has_teaching_note": bool((draft or {}).get("teaching_note")),
            },
        }
        buckets[label].append(ex)

    per_bucket = {
        "resolved_no_reply": 15,
        "open_no_reply_fyi": 10,
        "action_needed": 10,
        "resolved_manual": 8,
        "uncertain": 8,
    }

    def rank(ex: dict) -> int:
        s = 0
        sig = ex["signals"]
        if sig["cc_only"]:
            s += 4
        if sig["closing_heuristic"]:
            s += 3
        if ex["current_db_state"] == "NO_ACTION":
            s += 2
        if ex["subject"]:
            s += 1
        return s

    curated: list[dict] = []
    for label, limit in per_bucket.items():
        curated.extend(sorted(buckets[label], key=rank, reverse=True)[:limit])

    payload = {
        "dataset_version": "reply_disposition_v1",
        "generated_from": "postgres",
        "behavior_definitions": BEHAVIOR_SPEC,
        "stats": {
            "threads_with_inbound": len(rows),
            "suggested_bucket_counts": {k: len(v) for k, v in sorted(buckets.items())},
            "audit_event_types": {row[0]: row[1] for row in audit_counts},
        },
        "labeling_instructions": (
            "Set reviewer_label to one of the behavior_definitions keys when "
            "suggested_label is wrong. Use reviewer_notes for edge cases."
        ),
        "examples": curated,
    }

    _write_training_set(out_path, payload)
    return payload


def _write_training_set(out_path: Path, payload: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default="data/reply_disposition_training_v1.json",
        help="Output JSON path (relative to repo root or absolute)",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    out = Path(args.out)
    if not out.is_absolute():
        out = root / out
    payload = asyncio.run(export_training_set(out))
    print(f"Wrote {len(payload['examples'])} examples to {out}")
    print("Suggested buckets:", payload["stats"]["suggested_bucket_counts"])


if __name__ == "__main__":
    main()
