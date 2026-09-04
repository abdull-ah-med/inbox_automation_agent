"""DB tests for scope_decay_worker.sweep_expired.

Worked example — expires_at sweep:

Fixture:
  A. thread-scope atom: is_active=True, expires_at=1 day ago → should be deactivated.
  B. mailbox-scope atom: is_active=True, expires_at=None → must NOT be touched.
  C. already-inactive atom: is_active=False, expires_at=1 day ago → stays inactive.
  D. teaching note: status='active', expires_at=1 day ago → should be archived.
  E. teaching note: status='active', expires_at=None → must NOT be touched.

Oracle: hand-counted from the fixture — 1 atom deactivated (A), 1 note archived (D).
        B, C, E are unchanged per spec.

We call sweep_expired(session) directly rather than run_scope_decay() because the
scheduled wrapper creates its own session from the configured (non-test) factory.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.db.feedback_atom import FeedbackAtom
from app.models.db.teaching_note import TeachingNote
from app.workers.scope_decay_worker import sweep_expired

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
NOW = datetime.now(UTC)
PAST = NOW - timedelta(days=1)  # 1 day in the past → expired


def _atom(
    *,
    scope: str,
    is_active: bool,
    expires_at: datetime | None,
) -> FeedbackAtom:
    return FeedbackAtom(
        id=uuid.uuid4(),
        source_kind="urgency_edit",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX,
        atom_text=f"atom scope={scope}",
        atom_embedding=[0.0] * 1536,
        role="Fix",
        scope=scope,
        scope_key=f"{scope}:test",
        is_active=is_active,
        expires_at=expires_at,
    )


def _note(
    *,
    scope: str,
    status: str,
    expires_at: datetime | None,
) -> TeachingNote:
    return TeachingNote(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        title=f"Note scope={scope}",
        body="This is a teaching note body.",
        scope=scope,
        scope_key=f"{scope}:test",
        status=status,
        origin="manual",
        expires_at=expires_at,
    )


async def test_expired_thread_scope_atom_deactivated(db_session) -> None:
    """Thread-scope atom with past expires_at is set to is_active=False."""
    atom_a = _atom(scope="thread", is_active=True, expires_at=PAST)
    db_session.add(atom_a)
    await db_session.flush()

    await sweep_expired(db_session)

    refreshed = (
        await db_session.execute(select(FeedbackAtom).where(FeedbackAtom.id == atom_a.id))
    ).scalar_one()
    # Oracle: expired thread-scope atom → is_active=False
    assert refreshed.is_active is False


async def test_mailbox_scope_null_expires_untouched(db_session) -> None:
    """Mailbox-scope atom with expires_at=None is never deactivated."""
    atom_b = _atom(scope="mailbox", is_active=True, expires_at=None)
    db_session.add(atom_b)
    await db_session.flush()

    await sweep_expired(db_session)

    refreshed = (
        await db_session.execute(select(FeedbackAtom).where(FeedbackAtom.id == atom_b.id))
    ).scalar_one()
    # Oracle: no expiry set → is_active unchanged (True)
    assert refreshed.is_active is True


async def test_already_inactive_atom_stays_inactive(db_session) -> None:
    """Already-inactive atom is not re-processed (stays False, no side effects)."""
    atom_c = _atom(scope="thread", is_active=False, expires_at=PAST)
    db_session.add(atom_c)
    await db_session.flush()

    atoms_deactivated, _ = await sweep_expired(db_session)

    refreshed = (
        await db_session.execute(select(FeedbackAtom).where(FeedbackAtom.id == atom_c.id))
    ).scalar_one()
    # Oracle: already False → stays False (deactivated count does not include it)
    assert refreshed.is_active is False
    assert atoms_deactivated == 0  # no rows changed (already inactive)


async def test_expired_teaching_note_archived(db_session) -> None:
    """Active teaching note with past expires_at is archived."""
    note_d = _note(scope="sender_domain", status="active", expires_at=PAST)
    db_session.add(note_d)
    await db_session.flush()

    await sweep_expired(db_session)

    refreshed = (
        await db_session.execute(select(TeachingNote).where(TeachingNote.id == note_d.id))
    ).scalar_one()
    # Oracle: expired active note → status='archived'
    assert refreshed.status == "archived"


async def test_teaching_note_null_expires_untouched(db_session) -> None:
    """Active teaching note with expires_at=None is never archived."""
    note_e = _note(scope="mailbox", status="active", expires_at=None)
    db_session.add(note_e)
    await db_session.flush()

    _, notes_archived = await sweep_expired(db_session)

    refreshed = (
        await db_session.execute(select(TeachingNote).where(TeachingNote.id == note_e.id))
    ).scalar_one()
    # Oracle: no expiry → status unchanged ('active')
    assert refreshed.status == "active"
    assert notes_archived == 0
