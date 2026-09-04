"""DB tests for feedback_atom_repo.

Worked example:
  - Insert a Fix atom for source_kind="preference_pair", person_bound=True,
    scope="sender_domain", scope_key="domain:acme.com"
  - list_active_by_mailbox_scope returns exactly that 1 row
  - get_atom_by_id returns it with correct role="Fix"
  - deactivate_atom sets is_active=False; subsequent list returns 0 rows
"""

from __future__ import annotations

import uuid

import pytest

from app.repositories import feedback_atom_repo

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"


def _unit_vec(i: int) -> list[float]:
    v = [0.0] * 1536
    v[i % 1536] = 1.0
    return v


async def test_insert_fix_atom_person_bound(db_session) -> None:
    """Fix atom with person_bound=True is stored correctly."""
    source_id = uuid.uuid4()
    atom = await feedback_atom_repo.insert_atom(
        db_session,
        source_kind="preference_pair",
        source_id=source_id,
        mailbox=MAILBOX,
        atom_text="Always greet by first name",
        atom_embedding=_unit_vec(10),
        role="Fix",
        scope="sender_domain",
        scope_key="domain:acme.com",
        person_bound=True,
    )

    assert atom.role == "Fix"
    assert atom.person_bound is True
    assert atom.is_active is True
    assert atom.mailbox == MAILBOX
    assert atom.scope == "sender_domain"
    assert atom.scope_key == "domain:acme.com"
    assert atom.hit_count == 0
    # sender_domain auto-expires in 90 days (plan §4.8)
    assert atom.expires_at is not None


async def test_insert_thread_scope_has_no_expiry_until_resolved(db_session) -> None:
    """Thread-scope atoms do not auto-expire until the thread is resolved."""
    thread_atom = await feedback_atom_repo.insert_atom(
        db_session,
        source_kind="preference_pair",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX,
        atom_text="Only this thread",
        atom_embedding=_unit_vec(11),
        role="Fix",
        scope="thread",
        scope_key="thread:aaaaaaaa-0000-0000-0000-000000000001",
    )
    mailbox_atom = await feedback_atom_repo.insert_atom(
        db_session,
        source_kind="preference_pair",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX,
        atom_text="Mailbox habit",
        atom_embedding=_unit_vec(12),
        role="Fix",
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX}",
    )
    assert thread_atom.expires_at is None
    assert mailbox_atom.expires_at is None


async def test_list_active_by_mailbox_scope(db_session) -> None:
    """Only active atoms for the right scope/scope_key are returned."""
    source_id = uuid.uuid4()
    await feedback_atom_repo.insert_atom(
        db_session,
        source_kind="preference_pair",
        source_id=source_id,
        mailbox=MAILBOX,
        atom_text="Atom A",
        atom_embedding=_unit_vec(20),
        role="Fix",
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX}",
    )
    # Atom in different scope — must not appear
    await feedback_atom_repo.insert_atom(
        db_session,
        source_kind="preference_pair",
        source_id=source_id,
        mailbox=MAILBOX,
        atom_text="Atom B",
        atom_embedding=_unit_vec(21),
        role="Spec",
        scope="sender_domain",
        scope_key="domain:other.com",
    )

    rows = await feedback_atom_repo.list_active_by_mailbox_scope(
        db_session,
        mailbox=MAILBOX,
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX}",
    )
    # Exactly 1 active atom for the mailbox scope
    assert len(rows) == 1
    assert rows[0].atom_text == "Atom A"


async def test_deactivate_atom_removes_from_active_list(db_session) -> None:
    """deactivate_atom sets is_active=False; active list then returns 0 rows."""
    source_id = uuid.uuid4()
    atom = await feedback_atom_repo.insert_atom(
        db_session,
        source_kind="preference_pair",
        source_id=source_id,
        mailbox=MAILBOX,
        atom_text="Deactivate me",
        atom_embedding=_unit_vec(30),
        role="Fix",
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX}",
    )

    before = await feedback_atom_repo.list_active_by_mailbox_scope(
        db_session,
        mailbox=MAILBOX,
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX}",
    )
    assert len(before) == 1  # one active atom before deactivation

    await feedback_atom_repo.deactivate_atom(db_session, atom.id)

    after = await feedback_atom_repo.list_active_by_mailbox_scope(
        db_session,
        mailbox=MAILBOX,
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX}",
    )
    assert len(after) == 0  # deactivated atom no longer in active list


async def test_get_atom_by_id_returns_correct_role(db_session) -> None:
    """get_atom_by_id returns the atom with the correct role literal."""
    source_id = uuid.uuid4()
    atom = await feedback_atom_repo.insert_atom(
        db_session,
        source_kind="urgency_edit",
        source_id=source_id,
        mailbox=MAILBOX,
        atom_text="Only when the amount exceeds $1000",
        atom_embedding=_unit_vec(40),
        role="Spec",
        scope="thread",
        scope_key="thread:some-id",
        applies_when="when the invoice amount exceeds $1000",
    )

    fetched = await feedback_atom_repo.get_atom_by_id(db_session, atom.id)
    assert fetched is not None
    assert fetched.id == atom.id
    assert fetched.role == "Spec"
    assert fetched.applies_when == "when the invoice amount exceeds $1000"
