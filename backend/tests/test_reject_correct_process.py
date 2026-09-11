"""Reject with a process sentence pins it on the thread.

Worked example: SampleLab invoice — Elise writes what she would do; that exact
sentence is the thread pin. The why-note stays on the draft, not in pins.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.db.draft import Draft
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.repositories import draft_repo, thread_context_repo
from app.services import draft_feedback_service

pytestmark = pytest.mark.db

SALES = "sales@example.com"
T_OPEN = datetime(2026, 8, 18, 11, 0, tzinfo=UTC)
WHY = "DraftAssistant drafted a letter. This is an SampleLab invoice."
PROCESS = (
    "In this case I would process the SampleLab invoice and tell Beau the rebill is with Harmeyer."
)


async def _seed_draft(session, *, conversation_id: str) -> Draft:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=SALES,
        conversation_id=conversation_id,
        subject="SampleLabVendor invoice",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=T_OPEN,
    )
    session.add(thread)
    await session.flush()
    draft = Draft(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=str(uuid.uuid4()),
        subject=thread.subject,
        body="Happy to look into this invoice.",
        recipients={},
        teaching_note="Acknowledge the invoice.",
        created_at=T_OPEN,
    )
    session.add(draft)
    await session.commit()
    return draft


@pytest.mark.asyncio
async def test_reject_with_process_note_pins_that_sentence(db_session) -> None:
    draft = await _seed_draft(db_session, conversation_id="t-samplelab-process-pin")

    await draft_feedback_service.reject_draft(
        db_session,
        draft.id,
        feedback_note=WHY,
        reason_code="incomplete",
        actor="elise@example.com",
        process_note=PROCESS,
    )
    await db_session.commit()

    stored = await draft_repo.get_draft_by_id(db_session, draft.id)
    assert stored is not None
    assert stored.feedback_note == WHY
    pointer = await thread_context_repo.get(db_session, draft.thread_id)
    assert pointer is not None
    assert pointer.user_notes == PROCESS


@pytest.mark.asyncio
async def test_reject_process_note_appends_after_existing_pins(db_session) -> None:
    draft = await _seed_draft(db_session, conversation_id="t-samplelab-process-append")
    await thread_context_repo.get_or_create(db_session, draft.thread_id)
    await thread_context_repo.save_user_notes(
        db_session,
        draft.thread_id,
        notes="Do not CC legal",
        expected_version=0,
    )
    await db_session.commit()

    await draft_feedback_service.reject_draft(
        db_session,
        draft.id,
        feedback_note=WHY,
        reason_code="incomplete",
        actor="elise@example.com",
        process_note=PROCESS,
    )
    await db_session.commit()

    pointer = await thread_context_repo.get(db_session, draft.thread_id)
    assert pointer is not None
    assert pointer.user_notes == f"Do not CC legal\n\n{PROCESS}"


@pytest.mark.asyncio
async def test_reject_without_process_note_does_not_create_pins(db_session) -> None:
    draft = await _seed_draft(db_session, conversation_id="t-samplelab-no-process")

    await draft_feedback_service.reject_draft(
        db_session,
        draft.id,
        feedback_note=WHY,
        reason_code="incomplete",
        actor="elise@example.com",
    )
    await db_session.commit()

    pointer = await thread_context_repo.get(db_session, draft.thread_id)
    assert pointer is None or pointer.user_notes == ""
