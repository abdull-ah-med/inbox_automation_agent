"""Thread-level summaries for long InboxAssistant get_thread context.

Oracles: 5-message threshold, doubling refresh, and literal summary text
from a stubbed Haiku response — not recomputed by the service.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.services.thread_summary_service import should_refresh_thread_summary


def test_should_not_refresh_below_five_messages() -> None:
    assert should_refresh_thread_summary(message_count=4, stored_count=None) is False
    assert should_refresh_thread_summary(message_count=0, stored_count=None) is False


def test_should_refresh_at_five_messages_with_no_store() -> None:
    assert should_refresh_thread_summary(message_count=5, stored_count=None) is True


def test_should_not_refresh_until_count_doubles() -> None:
    assert should_refresh_thread_summary(message_count=5, stored_count=5) is False
    assert should_refresh_thread_summary(message_count=9, stored_count=5) is False
    assert should_refresh_thread_summary(message_count=10, stored_count=5) is True
    assert should_refresh_thread_summary(message_count=19, stored_count=10) is False
    assert should_refresh_thread_summary(message_count=20, stored_count=10) is True


def _settings() -> Settings:
    return Settings(
        environment="local",
        anthropic_api_key="sk-ant-test",
        classification_model="claude-haiku-4-5",
        target_mailboxes="sales@example.com",
        _env_file=None,
    )


def _haiku_client(text: str) -> AsyncMock:
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
        )
    )
    return client


async def _seed_thread(db_session, *, n: int) -> uuid.UUID:
    from app.models.db.message import Message
    from app.models.db.thread import Thread

    thread_id = uuid.uuid4()
    db_session.add(
        Thread(
            id=thread_id,
            mailbox="sales@example.com",
            conversation_id=f"conv-{thread_id}",
            subject="Invoice 42",
            state="NEW",
        )
    )
    await db_session.flush()
    start = datetime(2026, 8, 1, tzinfo=UTC)
    for i in range(n):
        db_session.add(
            Message(
                thread_id=thread_id,
                graph_message_id=str(uuid.uuid4()),
                direction="inbound" if i % 2 == 0 else "outbound",
                sender="alice@client.com" if i % 2 == 0 else "bob@sales.com",
                body_text=f"Message {i} about invoice 42.",
                received_at=start + timedelta(hours=i),
                to_recipients=["sales@example.com"],
                cc_recipients=[],
            )
        )
    await db_session.flush()
    return thread_id


@pytest.mark.asyncio
async def test_summary_generated_when_thread_reaches_five_messages(db_session) -> None:
    from sqlalchemy import select

    from app.models.db.thread_summary import ThreadSummary
    from app.services import thread_summary_service

    thread_id = await _seed_thread(db_session, n=5)
    expected = "Alice asked for invoice 42; Bob agreed to send Friday."
    client = _haiku_client(expected)

    stored = await thread_summary_service.maybe_refresh(
        db_session,
        thread_id=thread_id,
        client=client,
        settings=_settings(),
    )

    assert stored is not None
    assert stored.message_count == 5
    assert stored.summary_text == expected
    row = (
        await db_session.execute(select(ThreadSummary).where(ThreadSummary.thread_id == thread_id))
    ).scalar_one()
    assert row.summary_text == expected
    assert row.message_count == 5
    client.messages.create.assert_awaited()


@pytest.mark.asyncio
async def test_summary_regenerated_when_count_doubles(db_session) -> None:
    from app.models.db.message import Message
    from app.services import thread_summary_service

    thread_id = await _seed_thread(db_session, n=5)
    client = _haiku_client("First summary of invoice 42.")
    first = await thread_summary_service.maybe_refresh(
        db_session,
        thread_id=thread_id,
        client=client,
        settings=_settings(),
    )
    assert first is not None
    assert first.message_count == 5

    start = datetime(2026, 8, 10, tzinfo=UTC)
    for i in range(5):
        db_session.add(
            Message(
                thread_id=thread_id,
                graph_message_id=str(uuid.uuid4()),
                direction="inbound",
                sender="alice@client.com",
                body_text=f"Follow-up {i} still about invoice 42.",
                received_at=start + timedelta(hours=i),
                to_recipients=["sales@example.com"],
                cc_recipients=[],
            )
        )
    await db_session.flush()

    client = _haiku_client("Updated summary after ten messages.")
    second = await thread_summary_service.maybe_refresh(
        db_session,
        thread_id=thread_id,
        client=client,
        settings=_settings(),
    )
    assert second is not None
    assert second.message_count == 10
    assert second.summary_text == "Updated summary after ten messages."
