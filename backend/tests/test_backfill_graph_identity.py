"""Identity backfill lookback: only threads with mail in the window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.repositories import thread_repo
from scripts.backfill_graph_identity import load_threads_for_window

pytestmark = pytest.mark.db

NOW = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)
MAILBOX = "sampleagent@sample-site.example.com"


@pytest.mark.asyncio
async def test_seven_day_window_excludes_older_threads(db_session) -> None:
    """A thread last touched 10 days ago is outside a 7-day heal."""
    inside = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-identity-inside",
        subject="In window",
        last_message_at=NOW - timedelta(days=3),
    )
    outside = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-identity-outside",
        subject="Too old",
        last_message_at=NOW - timedelta(days=10),
    )
    await db_session.commit()

    rows = await load_threads_for_window(db_session, days=7, now=NOW)
    ids = {row.id for row in rows}
    assert inside.id in ids
    assert outside.id not in ids
