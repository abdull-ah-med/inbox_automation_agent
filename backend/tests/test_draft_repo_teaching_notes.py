"""Teaching note previews must not surface Outlook catch-up drafts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.db.draft import Draft
from app.repositories import draft_repo, thread_repo

pytestmark = pytest.mark.db

MAILBOX = "elise@example.com"
CATCHUP = "Learned from Outlook send"
LLM_NOTE = "Vendor clarified ElectronicClientID; restate the multi-tenant goal."


@pytest.mark.asyncio
async def test_latest_teaching_notes_skip_outlook_catchup(db_session) -> None:
    thread = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-teaching-catchup-1",
        subject="RE: Open items",
        last_message_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
    older = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    newer = older + timedelta(hours=1)
    db_session.add(
        Draft(
            id=uuid.uuid4(),
            thread_id=thread.id,
            message_id="AAMk-llm-teaching-1",
            subject="Re: Open items",
            body="Hi Beau,\n\nThank you for checking.",
            recipients={"suggested_recipients": [], "forward_to": None},
            teaching_note=LLM_NOTE,
            created_at=older,
        )
    )
    db_session.add(
        Draft(
            id=uuid.uuid4(),
            thread_id=thread.id,
            message_id="AAMk-catchup-teaching-1",
            subject="Re: Open items",
            body="Thanks for the follow-up Beau.",
            recipients={"suggested_recipients": [], "forward_to": None},
            teaching_note=CATCHUP,
            approval_note=CATCHUP,
            approved_at=newer,
            feedback_action="approve",
            created_at=newer,
        )
    )
    await db_session.commit()

    notes = await draft_repo.latest_teaching_notes_by_threads(db_session, [thread.id])
    assert notes[thread.id] == LLM_NOTE
