"""DB tests for teaching_note_repo.

Worked example:
  - Create 2 teaching notes for "sales@example.com", one active one paused
  - list_teaching_notes(status=None) returns 2 rows
  - list_teaching_notes(status="active") returns 1 row
  - update_teaching_note_status to "archived" -> status changes
  - get_teaching_note_by_id returns correct title
"""

from __future__ import annotations

import uuid

import pytest

from app.repositories import teaching_note_repo

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"


async def test_create_and_list_by_mailbox(db_session) -> None:
    """Two notes for same mailbox; list returns both regardless of status."""
    await teaching_note_repo.create_teaching_note(
        db_session,
        mailbox=MAILBOX,
        title="Always confirm scheduling",
        body="When the sender asks to schedule, confirm availability before offering times.",
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX}",
    )
    note2 = await teaching_note_repo.create_teaching_note(
        db_session,
        mailbox=MAILBOX,
        title="Billing tone rule",
        body="Use formal tone for billing disputes.",
        scope="mailbox+routing_category",
        scope_key=f"mailbox:{MAILBOX}:billing",
        origin="promoted_from_atom",
    )
    await teaching_note_repo.update_teaching_note_status(db_session, note2.id, "paused")

    all_notes = await teaching_note_repo.list_teaching_notes(db_session, mailbox=MAILBOX)
    assert len(all_notes) == 2  # hand-counted: two creates

    active_notes = await teaching_note_repo.list_teaching_notes(
        db_session, mailbox=MAILBOX, status="active"
    )
    assert len(active_notes) == 1  # only note1 remains active
    assert active_notes[0].title == "Always confirm scheduling"


async def test_get_by_id_returns_correct_fields(db_session) -> None:
    """get_teaching_note_by_id returns the note with correct title and origin."""
    note = await teaching_note_repo.create_teaching_note(
        db_session,
        mailbox=MAILBOX,
        title="Use first name",
        body="Address the sender by their first name in the opening.",
        scope="sender_domain",
        scope_key="domain:acme.com",
        person_bound=True,
    )

    fetched = await teaching_note_repo.get_teaching_note_by_id(db_session, note.id)
    assert fetched is not None
    assert fetched.id == note.id
    assert fetched.title == "Use first name"
    assert fetched.person_bound is True
    assert fetched.origin == "manual"
    assert fetched.status == "active"


async def test_update_status_to_archived(db_session) -> None:
    """update_teaching_note_status changes the status to the given value."""
    note = await teaching_note_repo.create_teaching_note(
        db_session,
        mailbox=MAILBOX,
        title="Temp rule",
        body="Temporary formatting rule.",
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX}",
    )
    assert note.status == "active"

    updated = await teaching_note_repo.update_teaching_note_status(db_session, note.id, "archived")
    assert updated is not None
    assert updated.status == "archived"
    assert updated.id == note.id


async def test_get_missing_id_returns_none(db_session) -> None:
    """get_teaching_note_by_id returns None for an unknown id."""
    result = await teaching_note_repo.get_teaching_note_by_id(db_session, uuid.uuid4())
    assert result is None
