"""DB test for drafts.retrieved_atom_ids and retrieved_note_ids columns.

Worked example:
  - Create a thread + draft with retrieved_atom_ids=[UUID_A, UUID_B]
  - Reload the draft from DB and assert the list equals exactly [UUID_A, UUID_B]
    (order preserved, no extra elements)
  - retrieved_note_ids defaults to None when not set
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.db.draft import Draft
from app.models.db.thread import Thread

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"

UUID_A = uuid.UUID("cccccccc-0000-0000-0000-000000000001")
UUID_B = uuid.UUID("cccccccc-0000-0000-0000-000000000002")


async def test_retrieved_atom_ids_persisted_and_reloaded(db_session) -> None:
    """Draft created with 2 retrieved_atom_ids; reload returns same 2 UUIDs."""
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id=str(uuid.uuid4()),
        subject="test draft retrieved ids",
        state="new",
    )
    db_session.add(thread)
    await db_session.flush()

    draft = Draft(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=str(uuid.uuid4()),
        subject="re: test",
        body="body",
        recipients={},
        teaching_note="",
        retrieved_atom_ids=[UUID_A, UUID_B],
    )
    db_session.add(draft)
    await db_session.flush()
    draft_id = draft.id

    # Expire the in-memory object so the next access hits the DB
    await db_session.commit()

    reloaded = (await db_session.execute(select(Draft).where(Draft.id == draft_id))).scalar_one()

    # Literal oracle: exactly [UUID_A, UUID_B], 2 elements
    assert reloaded.retrieved_atom_ids is not None
    assert len(reloaded.retrieved_atom_ids) == 2
    assert UUID_A in reloaded.retrieved_atom_ids
    assert UUID_B in reloaded.retrieved_atom_ids


async def test_retrieved_note_ids_defaults_to_none(db_session) -> None:
    """Draft created without retrieved_note_ids has None for that column."""
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id=str(uuid.uuid4()),
        subject="test default none",
        state="new",
    )
    db_session.add(thread)
    await db_session.flush()

    draft = Draft(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=str(uuid.uuid4()),
        subject="re: none",
        body="body",
        recipients={},
        teaching_note="",
    )
    db_session.add(draft)
    await db_session.flush()

    await db_session.commit()
    reloaded = (await db_session.execute(select(Draft).where(Draft.id == draft.id))).scalar_one()

    assert reloaded.retrieved_note_ids is None
