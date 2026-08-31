"""Read-only export of approved drafts from local Postgres into gitignored JSON.

Usage (from backend/):

    set -a && source .env && set +a
    .venv/bin/python -m tests.evals.exporters.db_approved_drafts

Never commits mailbox content — writes under ``tests/artifacts/evals/datasets/``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.llm.pii_redact import scrub_text
from tests.evals.dataset_io import ARTIFACTS_ROOT

_EXPORT_SQL = text(
    """
    SELECT
      d.id::text AS draft_id,
      t.mailbox,
      d.subject,
      d.body AS draft_body,
      d.edited_body,
      d.urgency,
      d.routing_category,
      d.context_match_confidence,
      d.feedback_action,
      d.approved_at,
      m.graph_message_id,
      coalesce(m.body_clean, m.body_text, '') AS inbound_body,
      m.sender AS inbound_sender
    FROM drafts d
    JOIN threads t ON t.id = d.thread_id
    JOIN messages m ON m.graph_message_id = d.message_id
    WHERE d.approved_at IS NOT NULL
    ORDER BY d.approved_at DESC
    LIMIT :limit
    """
)


def _row_to_case(row: Any) -> dict[str, Any]:
    gold = (row.edited_body or row.draft_body or "").strip()
    inbound = (row.inbound_body or "").strip()
    subject = (row.subject or "").strip()
    return {
        "id": f"db-approved-{row.draft_id}",
        "dataset_version": "db_approved_export",
        "suite_tags": ["B"] + (["A"] if row.context_match_confidence is not None else []),
        "subject": scrub_text(subject),
        "body": scrub_text(inbound),
        "mailbox": row.mailbox,
        "sender": scrub_text(row.inbound_sender or ""),
        "expected_output": scrub_text(gold),
        "use_samplelab_skill": False,
        "triage": {
            "is_spam": False,
            "has_action_items": True,
            "needs_context": row.context_match_confidence is not None,
            "routing_category": row.routing_category or "general",
            "action_items_summary": None,
        },
        "metadata": {
            "draft_id": row.draft_id,
            "graph_message_id": row.graph_message_id,
            "urgency": row.urgency,
            "context_match_confidence": row.context_match_confidence,
            "feedback_action": row.feedback_action,
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "used_edit": bool(row.edited_body and str(row.edited_body).strip()),
            "source": "local_db_approved_drafts",
        },
        "warning": (
            "Exported from local DB. Do not commit. May still contain residual PII "
            "after scrub_text — review before sharing."
        ),
    }


async def export_approved(*, limit: int = 50) -> Path:
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    cases: list[dict[str, Any]] = []
    try:
        async with engine.connect() as conn:
            result = await conn.execute(_EXPORT_SQL, {"limit": limit})
            for row in result.mappings():
                cases.append(_row_to_case(row))
    finally:
        await engine.dispose()

    out_dir = ARTIFACTS_ROOT / "datasets"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"approved_drafts_{stamp}.json"
    payload = {
        "exported_at": datetime.now(UTC).isoformat(),
        "count": len(cases),
        "cases": cases,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export approved drafts for LLM evals (read-only)")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    path = asyncio.run(export_approved(limit=args.limit))
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
