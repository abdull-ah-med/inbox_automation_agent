"""DB tests for thread_context_repo.

Worked example (Plan 3 §9):
  - Thread T has two facts inserted in order:
      "Check #11111 is for driver Ames"
      "Check #11111 is for driver Ames, cancelled"
  - After superseding the first with the second, list_active_facts returns
    exactly one row whose body is the second literal.
  - save_user_notes with a stale expected_version raises.
  - Pin save does not delete or rewrite facts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.exceptions import ThreadContextVersionConflict
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.repositories import thread_context_repo

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
PIN_TEXT = "Do not CC legal"
FACT_FIRST = "Check #11111 is for driver Ames"
FACT_SECOND = "Check #11111 is for driver Ames, cancelled"


async def _make_thread_with_message(session) -> tuple[Thread, Message]:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id=str(uuid.uuid4()),
        subject="CDLIS dispute",
        state="NEW",
    )
    session.add(thread)
    await session.flush()
    message = Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="buyer@acme.com",
        body_text="Please cancel check 11111",
        received_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        to_recipients=["sales@example.com"],
    )
    session.add(message)
    await session.flush()
    return thread, message


async def test_list_active_facts_omits_superseded_first_row(db_session) -> None:
    """After superseding the first fact, only the second body remains active."""
    thread, message = await _make_thread_with_message(db_session)
    await thread_context_repo.get_or_create(db_session, thread.id)

    first, second = await thread_context_repo.add_facts(
        db_session,
        thread.id,
        [
            {"body": FACT_FIRST, "source_message_id": message.id, "actor_kind": "llm"},
            {"body": FACT_SECOND, "source_message_id": message.id, "actor_kind": "llm"},
        ],
    )
    await thread_context_repo.supersede_facts(
        db_session,
        [first.id],
        superseded_by=second.id,
    )

    active = await thread_context_repo.list_active_facts(db_session, thread.id)
    assert [row.body for row in active] == [FACT_SECOND]


async def test_save_user_notes_stale_version_raises(db_session) -> None:
    """A second pin save with expected_version=0 fails after version 0 was consumed."""
    thread, _message = await _make_thread_with_message(db_session)
    row = await thread_context_repo.get_or_create(db_session, thread.id)
    assert row.version == 0
    assert row.user_notes == ""

    saved = await thread_context_repo.save_user_notes(
        db_session,
        thread.id,
        notes=PIN_TEXT,
        expected_version=0,
    )
    assert saved.user_notes == PIN_TEXT
    assert saved.version == 1

    with pytest.raises(ThreadContextVersionConflict):
        await thread_context_repo.save_user_notes(
            db_session,
            thread.id,
            notes="overwrite attempt",
            expected_version=0,
        )

    reread = await thread_context_repo.get(db_session, thread.id)
    assert reread is not None
    assert reread.user_notes == PIN_TEXT
    assert reread.version == 1


async def test_save_user_notes_does_not_touch_facts(db_session) -> None:
    """Pin save leaves the fact log unchanged, including superseded rows."""
    thread, message = await _make_thread_with_message(db_session)
    await thread_context_repo.get_or_create(db_session, thread.id)
    first, second = await thread_context_repo.add_facts(
        db_session,
        thread.id,
        [
            {"body": FACT_FIRST, "source_message_id": message.id, "actor_kind": "llm"},
            {"body": FACT_SECOND, "source_message_id": message.id, "actor_kind": "llm"},
        ],
    )
    await thread_context_repo.supersede_facts(
        db_session,
        [first.id],
        superseded_by=second.id,
    )

    await thread_context_repo.save_user_notes(
        db_session,
        thread.id,
        notes=PIN_TEXT,
        expected_version=0,
    )

    active = await thread_context_repo.list_active_facts(db_session, thread.id)
    assert [row.body for row in active] == [FACT_SECOND]


async def test_set_extract_hash_does_not_bump_pin_version(db_session) -> None:
    """Extract hash writes do not 409 the pin pointer."""
    thread, message = await _make_thread_with_message(db_session)
    await thread_context_repo.get_or_create(db_session, thread.id)
    await thread_context_repo.set_extract_hash(
        db_session,
        thread.id,
        extract_hash="a" * 64,
        last_message_id=message.id,
    )
    row = await thread_context_repo.get(db_session, thread.id)
    assert row is not None
    assert row.extract_input_hash == "a" * 64
    assert row.last_message_id_at_extract == message.id
    assert row.version == 0
    assert row.user_notes == ""


async def test_set_extract_status_visible_on_same_session_get(db_session) -> None:
    """A running status write must be readable without opening a new session.

    Production sessions use expire_on_commit=False; a stale identity map
    would make POST /rebuild return rebuild_in_progress false.
    """
    thread, _message = await _make_thread_with_message(db_session)
    await thread_context_repo.get_or_create(db_session, thread.id)
    started = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    await thread_context_repo.set_extract_status(
        db_session,
        thread.id,
        status="running",
        started_at=started,
        error=None,
    )

    row = await thread_context_repo.get(db_session, thread.id)
    assert row is not None
    assert row.extract_status == "running"
    assert row.extract_started_at == started
    assert row.last_extract_error is None
