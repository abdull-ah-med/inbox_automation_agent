"""Service tests for thread working-memory extract.

Oracles are Plan 3 §9 literals: hash skip leaves one fixture fact, coverage
re-extracts the outbound body, pins stay byte-for-byte, flag off writes nothing.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.repositories import thread_context_repo

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
PIN_TEXT = "Do not CC legal"
INBOUND_BODY = "Please cancel check 11111 for driver Ames"
OUTBOUND_BODY = "I cancelled check 11111 in the portal this morning"
FACT_INBOUND = "Sender asked to cancel check 11111 for driver Ames"
FACT_OUTBOUND = "Elise cancelled check 11111 in the portal"


def _settings(*, enabled: bool) -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        anthropic_api_key="sk-test",
        thread_context_enabled=enabled,
    )


def _haiku_response(facts: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps({"facts": facts}))]
    )


async def _seed_two_message_thread(session) -> tuple[Thread, Message, Message]:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id=str(uuid.uuid4()),
        subject="Cancel check 11111",
        state="NEW",
    )
    session.add(thread)
    await session.flush()
    inbound = Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="buyer@acme.com",
        body_text=INBOUND_BODY,
        received_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        to_recipients=[MAILBOX],
    )
    outbound = Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="outbound",
        sender=MAILBOX,
        body_text=OUTBOUND_BODY,
        received_at=datetime(2026, 9, 4, 12, 10, tzinfo=UTC),
        to_recipients=["buyer@acme.com"],
    )
    session.add_all([inbound, outbound])
    await session.flush()
    return thread, inbound, outbound


async def test_flag_off_extract_writes_zero_rows(db_session) -> None:
    from app.services import thread_context_service

    thread, _inbound, _outbound = await _seed_two_message_thread(db_session)
    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=AssertionError("Haiku must not run"))

    await thread_context_service.extract_if_needed(
        db_session,
        thread.id,
        client=client,
        settings=_settings(enabled=False),
        source="test",
    )

    assert await thread_context_repo.get(db_session, thread.id) is None
    assert await thread_context_repo.list_active_facts(db_session, thread.id) == []


async def test_hash_skip_second_extract_does_not_call_haiku(db_session) -> None:
    from app.services import thread_context_service

    thread, inbound, _outbound = await _seed_two_message_thread(db_session)
    await thread_context_repo.get_or_create(db_session, thread.id)
    await thread_context_repo.add_facts(
        db_session,
        thread.id,
        [{"body": FACT_INBOUND, "source_message_id": inbound.id, "actor_kind": "llm"}],
    )

    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_haiku_response([{"text": FACT_INBOUND, "source_message_id": str(inbound.id)}])
    )

    await thread_context_service.extract_if_needed(
        db_session,
        thread.id,
        client=client,
        settings=_settings(enabled=True),
        source="test",
    )
    first_calls = client.messages.create.await_count

    await thread_context_service.extract_if_needed(
        db_session,
        thread.id,
        client=client,
        settings=_settings(enabled=True),
        source="test",
    )

    assert client.messages.create.await_count == first_calls
    active = await thread_context_repo.list_active_facts(db_session, thread.id)
    assert [row.body for row in active] == [FACT_INBOUND]


async def test_coverage_reextracts_uncovered_outbound(db_session) -> None:
    from app.services import thread_context_service

    thread, inbound, outbound = await _seed_two_message_thread(db_session)
    client = AsyncMock()

    async def _create(**kwargs):
        user_content = kwargs["messages"][0]["content"]
        if OUTBOUND_BODY in user_content and INBOUND_BODY not in user_content:
            return _haiku_response([{"text": FACT_OUTBOUND, "source_message_id": str(outbound.id)}])
        return _haiku_response([{"text": FACT_INBOUND, "source_message_id": str(inbound.id)}])

    client.messages.create = AsyncMock(side_effect=_create)

    await thread_context_service.extract_if_needed(
        db_session,
        thread.id,
        client=client,
        settings=_settings(enabled=True),
        source="test",
    )

    assert client.messages.create.await_count == 2
    second_user = client.messages.create.await_args_list[1].kwargs["messages"][0]["content"]
    assert OUTBOUND_BODY in second_user
    assert INBOUND_BODY not in second_user

    active = await thread_context_repo.list_active_facts(db_session, thread.id)
    by_source = {row.source_message_id: row.body for row in active}
    assert by_source[inbound.id] == FACT_INBOUND
    assert by_source[outbound.id] == FACT_OUTBOUND


async def test_extract_preserves_pins_byte_for_byte(db_session) -> None:
    from app.services import thread_context_service

    thread, inbound, _outbound = await _seed_two_message_thread(db_session)
    await thread_context_repo.get_or_create(db_session, thread.id)
    await thread_context_repo.save_user_notes(
        db_session,
        thread.id,
        notes=PIN_TEXT,
        expected_version=0,
    )
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_haiku_response([{"text": FACT_INBOUND, "source_message_id": str(inbound.id)}])
    )

    await thread_context_service.extract_if_needed(
        db_session,
        thread.id,
        client=client,
        settings=_settings(enabled=True),
        source="test",
    )

    row = await thread_context_repo.get(db_session, thread.id)
    assert row is not None
    assert row.user_notes == PIN_TEXT


async def test_extract_does_not_persist_signature_or_gossip(db_session) -> None:
    from app.services import thread_context_service

    thread, inbound, outbound = await _seed_two_message_thread(db_session)
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_haiku_response(
            [
                {
                    "text": (
                        "Elise Chouest is Director at SampleSite with email "
                        "sampleagent@sample-site.example.com and phone (202) 555-0105 ext. 201."
                    ),
                    "source_message_id": str(inbound.id),
                },
                {
                    "text": "Sample Helpdesk reported that the integration is resolved.",
                    "source_message_id": str(outbound.id),
                },
                {
                    "text": FACT_INBOUND,
                    "source_message_id": str(inbound.id),
                },
            ]
        )
    )

    await thread_context_service.extract_if_needed(
        db_session,
        thread.id,
        client=client,
        settings=_settings(enabled=True),
        source="test",
    )

    active = await thread_context_repo.list_active_facts(db_session, thread.id)
    assert [row.body for row in active] == [FACT_INBOUND]


async def test_present_context_without_pointer_needs_initial_extract(db_session) -> None:
    """A thread that never ran extract must tell the UI to start it."""
    from app.services import thread_context_service

    thread, _inbound, _outbound = await _seed_two_message_thread(db_session)
    view = await thread_context_service.present_context(db_session, thread.id)

    assert view.needs_initial_extract is True
    assert view.rebuild_in_progress is False
    assert view.facts == []


async def test_present_context_after_hash_does_not_need_initial_extract(db_session) -> None:
    """Completed extract with zero visible facts is not 'never ran' — do not auto-rebuild."""
    from app.services import thread_context_service

    thread, inbound, _outbound = await _seed_two_message_thread(db_session)
    await thread_context_repo.get_or_create(db_session, thread.id)
    await thread_context_repo.set_extract_hash(
        db_session,
        thread.id,
        extract_hash="a" * 64,
        last_message_id=inbound.id,
    )

    view = await thread_context_service.present_context(db_session, thread.id)

    assert view.needs_initial_extract is False
    assert view.facts == []


async def test_present_context_running_does_not_need_initial_extract(db_session) -> None:
    from app.services import thread_context_service

    thread, _inbound, _outbound = await _seed_two_message_thread(db_session)
    await thread_context_service.begin_rebuild(db_session, thread.id)

    view = await thread_context_service.present_context(db_session, thread.id)

    assert view.rebuild_in_progress is True
    assert view.needs_initial_extract is False


async def test_present_context_failed_does_not_need_initial_extract(db_session) -> None:
    """A failed extract shows the error; the UI must not auto-retry in a loop."""
    from app.services import thread_context_service

    thread, _inbound, _outbound = await _seed_two_message_thread(db_session)
    await thread_context_service.begin_rebuild(db_session, thread.id)
    await thread_context_service.complete_rebuild_after_extract(db_session, thread.id, "failed")

    view = await thread_context_service.present_context(db_session, thread.id)

    assert view.needs_initial_extract is False
    assert view.rebuild_error == "Rebuild failed. Try again."


async def test_begin_rebuild_then_present_shows_in_progress(db_session) -> None:
    """Same expire_on_commit=False session as production GET-after-POST."""
    from app.services import thread_context_service

    thread, _inbound, _outbound = await _seed_two_message_thread(db_session)
    spawned = await thread_context_service.begin_rebuild(db_session, thread.id)
    await db_session.commit()

    view = await thread_context_service.present_context(db_session, thread.id)
    assert spawned is True
    assert view.rebuild_in_progress is True
    assert view.rebuild_error is None


async def test_second_begin_rebuild_while_running_is_rejected(db_session) -> None:
    """A second Rebuild click must not start another extract."""
    from app.services import thread_context_service

    thread, _inbound, _outbound = await _seed_two_message_thread(db_session)
    first = await thread_context_service.begin_rebuild(db_session, thread.id)
    second = await thread_context_service.begin_rebuild(db_session, thread.id)

    assert first is True
    assert second is False
    view = await thread_context_service.present_context(db_session, thread.id)
    assert view.rebuild_in_progress is True


async def test_finish_rebuild_clears_in_progress_and_keeps_facts(db_session) -> None:
    from app.services import thread_context_service

    thread, inbound, _outbound = await _seed_two_message_thread(db_session)
    await thread_context_service.begin_rebuild(db_session, thread.id)
    await thread_context_repo.add_facts(
        db_session,
        thread.id,
        [{"body": FACT_INBOUND, "source_message_id": inbound.id, "actor_kind": "llm"}],
    )
    await thread_context_service.finish_rebuild(db_session, thread.id, error=None)

    view = await thread_context_service.present_context(db_session, thread.id)
    assert view.rebuild_in_progress is False
    assert view.rebuild_error is None
    assert [fact.body for fact in view.facts] == [FACT_INBOUND]


async def test_stale_running_extract_is_failed_not_in_progress(db_session) -> None:
    """Running longer than 5 minutes is treated as failed so Rebuild can retry."""
    from app.services import thread_context_service

    thread, _inbound, _outbound = await _seed_two_message_thread(db_session)
    await thread_context_repo.get_or_create(db_session, thread.id)
    await thread_context_repo.set_extract_status(
        db_session,
        thread.id,
        status="running",
        started_at=datetime.now(UTC) - timedelta(minutes=6),
        error=None,
    )

    view = await thread_context_service.present_context(db_session, thread.id)
    assert view.rebuild_in_progress is False
    assert view.rebuild_error == "Rebuild timed out. Click Rebuild facts to try again."
    assert view.needs_initial_extract is False


async def test_locked_extract_leaves_rebuild_running(db_session) -> None:
    from app.services import thread_context_service

    thread, _inbound, _outbound = await _seed_two_message_thread(db_session)
    spawned = await thread_context_service.begin_rebuild(db_session, thread.id)
    assert spawned is True

    await thread_context_service.complete_rebuild_after_extract(db_session, thread.id, "locked")

    pointer = await thread_context_repo.get(db_session, thread.id)
    assert pointer is not None
    assert pointer.extract_status == "running"


async def test_failed_extract_marks_rebuild_failed(db_session) -> None:
    from app.services import thread_context_service

    thread, _inbound, _outbound = await _seed_two_message_thread(db_session)
    await thread_context_service.begin_rebuild(db_session, thread.id)
    await thread_context_service.complete_rebuild_after_extract(db_session, thread.id, "failed")

    pointer = await thread_context_repo.get(db_session, thread.id)
    assert pointer is not None
    assert pointer.extract_status == "failed"
    assert pointer.last_extract_error == "Rebuild failed. Try again."
