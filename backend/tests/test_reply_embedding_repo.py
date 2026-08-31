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
    row.original_email_preview = overrides.get("original_email_preview")
    row.learning_note = overrides.get("learning_note")
    row.is_excluded = overrides.get("is_excluded", False)
    row.created_at = overrides.get("created_at")
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


@pytest.mark.asyncio
async def test_set_excluded_updates_row() -> None:
    reply_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    updated = _embedding_row(id=reply_id, is_excluded=True)
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = updated
    list_result = MagicMock()
    list_result.all.return_value = [
        (updated, thread_id, "Re: Quote", "similar", "kelvin@example.com"),
    ]
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[update_result, list_result])
    session.flush = AsyncMock()

    stored = await reply_embedding_repo.set_excluded(
        session,
        reply_id,
        is_excluded=True,
    )
    assert stored is not None
    assert stored.is_excluded is True
    assert stored.thread_id == thread_id
    assert stored.draft_subject == "Re: Quote"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_excluded_missing_returns_none() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    assert (
        await reply_embedding_repo.set_excluded(
            session,
            uuid.uuid4(),
            is_excluded=True,
        )
        is None
    )


@pytest.mark.asyncio
async def test_list_reply_embeddings() -> None:
    rows = [_embedding_row(), _embedding_row(is_excluded=True)]
    result = MagicMock()
    result.all.return_value = [
        (rows[0], uuid.uuid4(), "First subject", "once", "a@example.com"),
        (rows[1], uuid.uuid4(), "Second subject", None, None),
    ]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    listed = await reply_embedding_repo.list_reply_embeddings(
        session,
        mailbox="elise@example.com",
        limit=10,
    )
    assert len(listed) == 2
    assert listed[0].draft_subject == "First subject"
    assert listed[1].is_excluded is True


@pytest.mark.asyncio
async def test_missing_mailbox_returns_empty_or_raises() -> None:
    """H13: listing without a mailbox must not return cross-tenant rows."""
    session = AsyncMock()
    with pytest.raises(ValueError, match="mailbox is required"):
        await reply_embedding_repo.list_reply_embeddings(session, mailbox="")
