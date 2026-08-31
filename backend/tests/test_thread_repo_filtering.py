"""Unit tests for mailbox thread listing in ``thread_repo``.

Verifies SPAM/NO_ACTION are included by default, optional date bounds on
``last_message_at``, and the ``set_thread_outcome`` write path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.schemas.email import ThreadStateEnum
from app.repositories import thread_repo


class _EmptyResult:
    def all(self) -> list[object]:
        return []

    def scalar_one_or_none(self) -> None:
        return None


class _CapturingSession:
    """Records every statement passed to ``execute`` without hitting a DB."""

    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, stmt: object) -> _EmptyResult:
        self.statements.append(stmt)
        return _EmptyResult()

    async def flush(self) -> None:
        return None


def _compiled(stmt: object) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_list_by_mailbox_includes_filtered_states_by_default() -> None:
    session = _CapturingSession()
    await thread_repo.list_by_mailbox(session, "elise@example.com")  # type: ignore[arg-type]

    sql = _compiled(session.statements[0])
    assert "threads.state NOT IN" not in sql


@pytest.mark.asyncio
async def test_list_by_mailbox_explicit_state_filters_single_state() -> None:
    session = _CapturingSession()
    await thread_repo.list_by_mailbox(  # type: ignore[arg-type]
        session, "elise@example.com", state=ThreadStateEnum.SPAM.value
    )

    sql = _compiled(session.statements[0])
    assert "threads.state NOT IN" not in sql
    assert "threads.state = 'SPAM'" in sql


@pytest.mark.asyncio
async def test_list_by_mailbox_date_from_bounds_last_message_at() -> None:
    session = _CapturingSession()
    await thread_repo.list_by_mailbox(  # type: ignore[arg-type]
        session,
        "elise@example.com",
        date_from=date(2026, 8, 1),
    )

    sql = _compiled(session.statements[0])
    assert "threads.last_message_at >= '2026-08-01" in sql


@pytest.mark.asyncio
async def test_list_by_mailbox_date_to_is_exclusive_end_of_day() -> None:
    session = _CapturingSession()
    await thread_repo.list_by_mailbox(  # type: ignore[arg-type]
        session,
        "elise@example.com",
        date_to=date(2026, 8, 28),
    )

    sql = _compiled(session.statements[0])
    assert "threads.last_message_at < '2026-08-29" in sql


def _fake_thread_row(thread_id: uuid.UUID, *, state: str, urgency: str | None) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=thread_id,
        mailbox="elise@example.com",
        conversation_id="c1",
        subject="Subject",
        state=state,
        urgency=urgency,
        category=None,
        last_message_at=now,
        last_updated_at=now,
    )


def _session_returning(row: object) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_set_thread_outcome_sets_state_and_urgency() -> None:
    thread_id = uuid.uuid4()
    row = _fake_thread_row(thread_id, state=ThreadStateEnum.DRAFTED.value, urgency="HIGH")
    session = _session_returning(row)

    updated = await thread_repo.set_thread_outcome(
        session, thread_id, state=ThreadStateEnum.DRAFTED.value, urgency="HIGH"
    )

    session.execute.assert_awaited_once()
    stmt = session.execute.await_args.args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "UPDATE threads" in sql
    assert "'HIGH'" in sql
    assert updated is not None
    assert updated.state == "DRAFTED"
    assert updated.urgency == "HIGH"


@pytest.mark.asyncio
async def test_set_thread_outcome_leaves_urgency_untouched_when_none() -> None:
    thread_id = uuid.uuid4()
    row = _fake_thread_row(thread_id, state=ThreadStateEnum.SPAM.value, urgency=None)
    session = _session_returning(row)

    await thread_repo.set_thread_outcome(session, thread_id, state=ThreadStateEnum.SPAM.value)

    stmt = session.execute.await_args.args[0]
    # SQLAlchemy Update.values() only includes columns explicitly passed in.
    assert "urgency" not in stmt._values  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_set_thread_outcome_returns_none_when_thread_missing() -> None:
    """Best-effort update: a concurrent delete must not raise."""
    session = _session_returning(None)

    result = await thread_repo.set_thread_outcome(
        session, uuid.uuid4(), state=ThreadStateEnum.SPAM.value
    )

    assert result is None
