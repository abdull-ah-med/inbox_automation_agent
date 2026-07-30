"""Unit tests for reply_embedding_repo."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories import reply_embedding_repo


def _embedding_row(**overrides: object) -> MagicMock:
    row = MagicMock()
    row.id = overrides.get("id", uuid.uuid4())
    row.draft_id = overrides.get("draft_id", uuid.uuid4())
    row.mailbox = overrides.get("mailbox", "elise@example.com")
    row.reply_text = overrides.get("reply_text", "Thanks — sending now.")
    row.original_email_preview = overrides.get("original_email_preview", None)
    return row


@pytest.mark.asyncio
async def test_store_returns_existing_when_present() -> None:
    draft_id = uuid.uuid4()
    existing = _embedding_row(draft_id=draft_id)
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    stored = await reply_embedding_repo.store_reply_embedding(
        session,
        draft_id=draft_id,
        mailbox="elise@example.com",
        embedding=[0.1, 0.2],
        reply_text="ignored",
    )
    assert stored.draft_id == draft_id
    assert stored.reply_text == "Thanks — sending now."
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_inserts_when_missing() -> None:
    draft_id = uuid.uuid4()
    inserted = _embedding_row(draft_id=draft_id, reply_text="New reply")
    miss = MagicMock()
    miss.scalar_one_or_none.return_value = None
    insert_result = MagicMock()
    insert_result.scalar_one_or_none.return_value = inserted
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[miss, insert_result])
    session.flush = AsyncMock()

    stored = await reply_embedding_repo.store_reply_embedding(
        session,
        draft_id=draft_id,
        mailbox="elise@example.com",
        embedding=[0.1, 0.2],
        reply_text="New reply",
    )
    assert stored.reply_text == "New reply"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_conflict_refetches() -> None:
    draft_id = uuid.uuid4()
    existing = _embedding_row(draft_id=draft_id, reply_text="Race winner")
    miss = MagicMock()
    miss.scalar_one_or_none.return_value = None
    empty_insert = MagicMock()
    empty_insert.scalar_one_or_none.return_value = None
    refetch = MagicMock()
    refetch.scalar_one.return_value = existing
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[miss, empty_insert, refetch])

    stored = await reply_embedding_repo.store_reply_embedding(
        session,
        draft_id=draft_id,
        mailbox="elise@example.com",
        embedding=[0.1],
        reply_text="loser",
    )
    assert stored.reply_text == "Race winner"


@pytest.mark.asyncio
async def test_find_similar_empty_limit() -> None:
    session = AsyncMock()
    assert (
        await reply_embedding_repo.find_similar_replies(
            session,
            query_embedding=[0.1],
            limit=0,
        )
        == []
    )
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_find_similar_with_mailbox() -> None:
    result = MagicMock()
    row = MagicMock()
    row.reply_text = "Similar reply"
    result.all.return_value = [row]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    texts = await reply_embedding_repo.find_similar_replies(
        session,
        query_embedding=[0.1, 0.2],
        mailbox="elise@example.com",
        limit=3,
    )
    assert texts == ["Similar reply"]
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_similar_without_mailbox() -> None:
    result = MagicMock()
    row = MagicMock()
    row.reply_text = "Any mailbox"
    result.all.return_value = [row]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    texts = await reply_embedding_repo.find_similar_replies(
        session,
        query_embedding=[0.1, 0.2],
        mailbox=None,
        limit=2,
    )
    assert texts == ["Any mailbox"]
