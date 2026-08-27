"""Upsert must not regress last_message_at when older conversation sync arrives."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.repositories import thread_repo

pytestmark = pytest.mark.db

MAILBOX = "elise@example.com"
NEWER = datetime(2026, 8, 28, 0, 25, tzinfo=UTC)
OLDER = datetime(2026, 8, 27, 19, 23, tzinfo=UTC)


@pytest.mark.asyncio
async def test_upsert_thread_keeps_newer_last_message_at(db_session) -> None:
    first = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-greatest-1",
        subject="RE: Open items",
        last_message_at=NEWER,
    )
    await db_session.commit()

    second = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-greatest-1",
        subject="RE: Open items",
        last_message_at=OLDER,
    )
    await db_session.commit()

    assert second.id == first.id
    assert second.last_message_at == NEWER


@pytest.mark.asyncio
async def test_upsert_thread_advances_when_incoming_is_newer(db_session) -> None:
    first = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-greatest-2",
        subject="RE: Open items",
        last_message_at=OLDER,
    )
    await db_session.commit()

    second = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-greatest-2",
        subject="RE: Open items",
        last_message_at=NEWER,
    )
    await db_session.commit()

    assert second.id == first.id
    assert second.last_message_at == NEWER
