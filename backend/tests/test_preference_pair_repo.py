"""DB tests for preference_pair_repo.

Oracles are hand-counted literals from the worked fixture, not derived from the
repo functions themselves.

Worked example:
  - Insert one approve pair for draft D1 in mailbox "sales@example.com"
    with routing_category="billing", decision="approve", sender_domain="acme.com"
  - get_by_draft_id(D1) must return exactly that row
  - list_by_mailbox("sales@example.com") must return exactly 1 row
  - Insert a second pair for draft D2 (reject)
  - list_by_mailbox returns 2 rows newest-first
  - Attempting to insert another pair for D1 (unique constraint) returns the
    existing row without raising
"""

from __future__ import annotations

import hashlib
import uuid

import pytest

from app.models.db.draft import Draft
from app.models.db.thread import Thread
from app.repositories import preference_pair_repo

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"


def _unit_vec(i: int) -> list[float]:
    v = [0.0] * 1536
    v[i % 1536] = 1.0
    return v


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def _make_thread_and_draft(session, mailbox: str) -> tuple[Thread, Draft]:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=mailbox,
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


async def test_insert_and_get_by_draft_id(db_session) -> None:
    """Inserted pair is retrievable by draft_id with correct decision and mailbox."""
    thread, draft = await _make_thread_and_draft(db_session, MAILBOX)

    pair = await preference_pair_repo.insert_preference_pair(
        db_session,
        draft_id=draft.id,
        thread_id=thread.id,
        mailbox=MAILBOX,
        sender_address="buyer@acme.com",
        sender_domain="acme.com",
        routing_category="billing",
        email_text_hash=_hash("hello"),
        email_embedding=_unit_vec(0),
        decision="approve",
        chosen_body="Great, confirmed.",
    )

    assert pair.decision == "approve"
    assert pair.mailbox == MAILBOX
    assert pair.sender_domain == "acme.com"
    assert pair.routing_category == "billing"
    assert pair.chosen_body == "Great, confirmed."
    assert pair.rejected_body is None
    assert pair.draft_id == draft.id

    fetched = await preference_pair_repo.get_by_draft_id(db_session, draft.id)
    assert fetched is not None
    assert fetched.id == pair.id
    assert fetched.decision == "approve"


async def test_list_by_mailbox_returns_correct_count(db_session) -> None:
    """list_by_mailbox returns exactly 2 rows after two inserts."""
    _, d1 = await _make_thread_and_draft(db_session, MAILBOX)
    t2, d2 = await _make_thread_and_draft(db_session, MAILBOX)

    await preference_pair_repo.insert_preference_pair(
        db_session,
        draft_id=d1.id,
        thread_id=d1.thread_id,
        mailbox=MAILBOX,
        sender_address="a@acme.com",
        sender_domain="acme.com",
        routing_category="billing",
        email_text_hash=_hash("email one"),
        email_embedding=_unit_vec(1),
        decision="approve",
    )
    await preference_pair_repo.insert_preference_pair(
        db_session,
        draft_id=d2.id,
        thread_id=t2.id,
        mailbox=MAILBOX,
        sender_address="b@acme.com",
        sender_domain="acme.com",
        routing_category="billing",
        email_text_hash=_hash("email two"),
        email_embedding=_unit_vec(2),
        decision="reject",
        rejected_body="Sorry, wrong tone.",
    )

    rows = await preference_pair_repo.list_by_mailbox(db_session, mailbox=MAILBOX)
    assert len(rows) == 2  # hand-counted: two inserts, one mailbox


async def test_unique_draft_id_idempotent(db_session) -> None:
    """Re-inserting with the same draft_id returns the original row without error."""
    _, draft = await _make_thread_and_draft(db_session, MAILBOX)

    first = await preference_pair_repo.insert_preference_pair(
        db_session,
        draft_id=draft.id,
        thread_id=draft.thread_id,
        mailbox=MAILBOX,
        sender_address="x@y.com",
        sender_domain="y.com",
        routing_category="sales",
        email_text_hash=_hash("dup"),
        email_embedding=_unit_vec(3),
        decision="approve",
    )
    second = await preference_pair_repo.insert_preference_pair(
        db_session,
        draft_id=draft.id,
        thread_id=draft.thread_id,
        mailbox=MAILBOX,
        sender_address="x@y.com",
        sender_domain="y.com",
        routing_category="sales",
        email_text_hash=_hash("dup"),
        email_embedding=_unit_vec(3),
        decision="reject",  # different decision — must be ignored
    )
    # Both references must point to the same row
    assert first.id == second.id
    assert second.decision == "approve"  # original, not the conflicting value


async def test_get_by_draft_id_missing_returns_none(db_session) -> None:
    """get_by_draft_id returns None for an unknown draft_id."""
    result = await preference_pair_repo.get_by_draft_id(db_session, uuid.uuid4())
    assert result is None
