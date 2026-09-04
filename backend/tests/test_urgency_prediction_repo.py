"""DB tests for urgency_prediction_repo.

Worked example:
  - Insert a prediction for draft D1 with probs literal
    {"LOW": 0.02, "NORMAL": 0.71, "HIGH": 0.24, "CRITICAL": 0.03}
  - get_urgency_prediction_by_draft_id returns exactly those probs
  - predicted_urgency == "NORMAL" (argmax of the literal above)
  - Re-inserting for the same draft_id is idempotent
"""

from __future__ import annotations

import uuid

import pytest

from app.models.db.draft import Draft
from app.models.db.thread import Thread
from app.repositories import urgency_prediction_repo

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
# Hand-counted probs — argmax is NORMAL at 0.71
PROBS = {"LOW": 0.02, "NORMAL": 0.71, "HIGH": 0.24, "CRITICAL": 0.03}


async def _make_thread_and_draft(session) -> tuple[Thread, Draft]:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id=str(uuid.uuid4()),
        subject="test",
        state="new",
    )
    session.add(thread)
    await session.flush()

    draft = Draft(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=str(uuid.uuid4()),
        subject="re: test",
        body="draft body",
        recipients={},
        teaching_note="",
    )
    session.add(draft)
    await session.flush()
    return thread, draft


async def test_insert_and_get_probs_literal(db_session) -> None:
    """Inserted probs are stored exactly as provided; no rounding or reformat."""
    thread, draft = await _make_thread_and_draft(db_session)

    pred = await urgency_prediction_repo.insert_urgency_prediction(
        db_session,
        draft_id=draft.id,
        thread_id=thread.id,
        mailbox=MAILBOX,
        sender_domain="vendor.com",
        predicted_urgency="NORMAL",
        probs=PROBS,
        final_urgency="NORMAL",
        routing_category="billing",
    )

    assert pred.predicted_urgency == "NORMAL"
    assert pred.final_urgency == "NORMAL"
    assert pred.mailbox == MAILBOX
    assert pred.sender_domain == "vendor.com"

    # Literal oracle: exact probs dict
    assert pred.probs == {"LOW": 0.02, "NORMAL": 0.71, "HIGH": 0.24, "CRITICAL": 0.03}


async def test_get_by_draft_id_returns_correct_row(db_session) -> None:
    """get_urgency_prediction_by_draft_id returns row for the correct draft."""
    thread, draft = await _make_thread_and_draft(db_session)

    await urgency_prediction_repo.insert_urgency_prediction(
        db_session,
        draft_id=draft.id,
        thread_id=thread.id,
        mailbox=MAILBOX,
        sender_domain="vendor.com",
        predicted_urgency="HIGH",
        probs={"LOW": 0.0, "NORMAL": 0.1, "HIGH": 0.8, "CRITICAL": 0.1},
        final_urgency="HIGH",
    )

    fetched = await urgency_prediction_repo.get_urgency_prediction_by_draft_id(db_session, draft.id)
    assert fetched is not None
    assert fetched.draft_id == draft.id
    assert fetched.predicted_urgency == "HIGH"


async def test_idempotent_insert(db_session) -> None:
    """Re-inserting for the same draft_id returns the original row unchanged."""
    thread, draft = await _make_thread_and_draft(db_session)

    first = await urgency_prediction_repo.insert_urgency_prediction(
        db_session,
        draft_id=draft.id,
        thread_id=thread.id,
        mailbox=MAILBOX,
        sender_domain="vendor.com",
        predicted_urgency="NORMAL",
        probs=PROBS,
        final_urgency="NORMAL",
    )
    second = await urgency_prediction_repo.insert_urgency_prediction(
        db_session,
        draft_id=draft.id,
        thread_id=thread.id,
        mailbox=MAILBOX,
        sender_domain="vendor.com",
        predicted_urgency="HIGH",  # conflicting — must be ignored
        probs={"LOW": 0.0, "NORMAL": 0.0, "HIGH": 1.0, "CRITICAL": 0.0},
        final_urgency="HIGH",
    )

    assert first.id == second.id
    assert second.predicted_urgency == "NORMAL"  # original value preserved


async def test_get_missing_returns_none(db_session) -> None:
    """get_urgency_prediction_by_draft_id returns None for unknown draft_id."""
    result = await urgency_prediction_repo.get_urgency_prediction_by_draft_id(
        db_session, uuid.uuid4()
    )
    assert result is None
