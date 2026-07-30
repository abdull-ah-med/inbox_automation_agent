"""Tests for approved reply memory (store + retrieve)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.services import reply_memory_service


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "sk-test",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 1536,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_store_approved_reply_embeds_and_persists() -> None:
    session = AsyncMock()
    client = AsyncMock()
    draft_id = uuid.uuid4()

    with (
        patch(
            "app.services.reply_memory_service.embedding_service.embed_text",
            AsyncMock(return_value=[0.1] * 1536),
        ) as embed_mock,
        patch(
            "app.services.reply_memory_service.reply_embedding_repo.store_reply_embedding",
            AsyncMock(),
        ) as store_mock,
    ):
        await reply_memory_service.store_approved_reply(
            session,
            openai_client=client,
            settings=_settings(),
            draft_id=draft_id,
            mailbox="elise@example.com",
            final_body="Thanks — I'll send the packet today.",
        )

    embed_mock.assert_awaited_once()
    store_mock.assert_awaited_once()
    assert store_mock.await_args.kwargs["draft_id"] == draft_id


@pytest.mark.asyncio
async def test_store_skips_without_openai() -> None:
    session = AsyncMock()
    with patch(
        "app.services.reply_memory_service.embedding_service.embed_text",
        AsyncMock(),
    ) as embed_mock:
        await reply_memory_service.store_approved_reply(
            session,
            openai_client=None,
            settings=_settings(openai_api_key=""),
            draft_id=uuid.uuid4(),
            mailbox="elise@example.com",
            final_body="Hello",
        )
    embed_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_failure_does_not_raise() -> None:
    session = AsyncMock()
    with patch(
        "app.services.reply_memory_service.embedding_service.embed_text",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await reply_memory_service.store_approved_reply(
            session,
            openai_client=AsyncMock(),
            settings=_settings(),
            draft_id=uuid.uuid4(),
            mailbox="elise@example.com",
            final_body="Hello",
        )


@pytest.mark.asyncio
async def test_find_similar_replies_returns_texts() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.reply_memory_service.embedding_service.embed_text",
            AsyncMock(return_value=[0.2] * 1536),
        ),
        patch(
            "app.services.reply_memory_service.reply_embedding_repo.find_similar_replies",
            AsyncMock(return_value=["Past reply A", "Past reply B"]),
        ),
    ):
        result = await reply_memory_service.find_similar_replies(
            session,
            openai_client=AsyncMock(),
            settings=_settings(),
            email_text="Need docs",
            mailbox="elise@example.com",
            limit=3,
        )
    assert result == ["Past reply A", "Past reply B"]


@pytest.mark.asyncio
async def test_find_returns_empty_on_failure() -> None:
    session = AsyncMock()
    with patch(
        "app.services.reply_memory_service.embedding_service.embed_text",
        AsyncMock(side_effect=RuntimeError("down")),
    ):
        result = await reply_memory_service.find_similar_replies(
            session,
            openai_client=AsyncMock(),
            settings=_settings(),
            email_text="Need docs",
        )
    assert result == []


@pytest.mark.asyncio
async def test_find_returns_empty_without_openai() -> None:
    result = await reply_memory_service.find_similar_replies(
        AsyncMock(),
        openai_client=None,
        settings=_settings(openai_api_key=""),
        email_text="Need docs",
    )
    assert result == []


@pytest.mark.asyncio
async def test_find_similar_replies_repo_orders_by_distance() -> None:
    from app.repositories import reply_embedding_repo

    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = [
        MagicMock(reply_text="Closest"),
        MagicMock(reply_text="Next"),
    ]
    session.execute = AsyncMock(return_value=result)

    texts = await reply_embedding_repo.find_similar_replies(
        session,
        query_embedding=[0.0] * 1536,
        limit=2,
    )
    assert texts == ["Closest", "Next"]
