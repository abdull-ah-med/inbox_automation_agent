"""Unit tests for OpenAI embedding service (SDK mocked)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.services import embedding_service


def _email(**overrides: object) -> EmailMessageSchema:
    base: dict[str, object] = {
        "message_id": "graph-m1",
        "conversation_id": "c1",
        "mailbox": "elise@example.com",
        "sender": "vendor@example.com",
        "subject": "Need docs",
        "body_text": "Please send the packet. SSN 123-45-6789",
        "body_preview": "Please send the packet.",
        "received_at": datetime(2026, 7, 22, tzinfo=UTC),
        "direction": EmailDirectionEnum.INBOUND,
        "to_recipients": ["elise@example.com"],
        "cc_recipients": ["ops@example.com"],
    }
    base.update(overrides)
    return EmailMessageSchema(**base)  # type: ignore[arg-type]


def _settings(**overrides: object) -> Settings:
    values = {
        "environment": "local",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 8,  # small for tests
        "openai_api_key": "test-key",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _vector(dim: int, fill: float = 0.1) -> list[float]:
    return [fill] * dim


@pytest.mark.asyncio
async def test_embed_email_calls_openai_with_model_and_dimensions() -> None:
    settings = _settings()
    dim = settings.embedding_dimension
    client = AsyncMock()
    client.embeddings.create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=_vector(dim))])
    )

    vector = await embedding_service.embed_email(_email(), client=client, settings=settings)

    assert len(vector) == dim
    kwargs = client.embeddings.create.await_args.kwargs
    assert kwargs["model"] == "text-embedding-3-small"
    assert kwargs["encoding_format"] == "float"
    assert kwargs["dimensions"] == dim
    assert "123-45-6789" not in kwargs["input"]
    assert "[REDACTED_SSN]" in kwargs["input"]
    assert "Subject:" in kwargs["input"]


@pytest.mark.asyncio
async def test_embed_email_rejects_dimension_mismatch() -> None:
    settings = _settings(embedding_dimension=8)
    client = AsyncMock()
    client.embeddings.create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=_vector(4))])
    )

    with pytest.raises(ValueError, match="dimension mismatch"):
        await embedding_service.embed_email(_email(), client=client, settings=settings)


@pytest.mark.asyncio
async def test_embed_and_store_safe_swallows_failures() -> None:
    settings = _settings()
    client = AsyncMock()
    client.embeddings.create = AsyncMock(side_effect=RuntimeError("boom"))
    session = AsyncMock()

    result = await embedding_service.embed_and_store_safe(
        session,
        email=_email(),
        client=client,
        settings=settings,
    )
    assert result is None


@pytest.mark.asyncio
async def test_embed_and_store_persists_via_repo() -> None:
    settings = _settings()
    dim = settings.embedding_dimension
    client = AsyncMock()
    client.embeddings.create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=_vector(dim))])
    )
    session = AsyncMock()
    stored = EmbeddingMatchSchema(
        id=__import__("uuid").uuid4(),
        conversation_id="c1",
        similarity_score=1.0,
        message_id=None,
    )

    with patch(
        "app.services.embedding_service.embedding_repo.insert_embedding",
        new=AsyncMock(return_value=stored),
    ) as insert:
        result = await embedding_service.embed_and_store(
            session,
            email=_email(),
            client=client,
            settings=settings,
        )

    assert result is stored
    insert.assert_awaited_once()
    kwargs = insert.await_args.kwargs
    assert kwargs["mailbox"] == "elise@example.com"
    assert kwargs["sender_email"] == "vendor@example.com"
    assert kwargs["recipient_emails"] == ["elise@example.com"]
    assert kwargs["cc_emails"] == ["ops@example.com"]
    assert kwargs["graph_message_id"] == "graph-m1"
    assert len(kwargs["embedding"]) == dim


@pytest.mark.asyncio
async def test_build_embed_text_truncates() -> None:
    long_body = "x" * (embedding_service._MAX_EMBED_CHARS + 500)
    text = embedding_service._build_embed_text(_email(body_text=long_body, subject="Hi"))
    assert len(text) <= embedding_service._MAX_EMBED_CHARS


def test_body_preview_for_store_falls_back() -> None:
    preview = embedding_service._body_preview_for_store(
        _email(body_preview=None, body_text="", subject="Only subject")
    )
    assert preview == "Only subject"
