"""InboxAssistant latency SLOs with LLM mocked at the client boundary."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.llm.chat import ChatAgentResult
from app.models.schemas.search import SearchHit

SALES = "sales@example.com"
THREAD_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
NEAR = [1.0] + [0.0] * 1535


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=SALES,
        anthropic_api_key="sk-ant-test",
        openai_api_key="sk-test",
        chat_model="claude-haiku-4-5",
        chat_semantic_cache_enabled=True,
        database_url=("postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test"),
        redis_url="redis://localhost:6379/15",
    )


class _CacheHit:
    response_json = {
        "answer": "Focus on Invoice dispute — overdue billing.",
        "citations": [],
        "retrieval_count": 1,
        "mailbox": SALES,
        "refused_write": False,
    }
    similarity = 0.96
    id = uuid.uuid4()


@pytest.mark.asyncio
async def test_cache_hit_ttft_under_200ms() -> None:
    from app.services import chat_service

    anthropic = MagicMock()
    anthropic.messages.create = AsyncMock(
        side_effect=AssertionError("cache hit must not call Claude")
    )
    with (
        patch(
            "app.services.chat_service.embedding_service.embed_text",
            AsyncMock(return_value=NEAR),
        ),
        patch(
            "app.services.chat_service.chat_cache_repo.find_semantic_hit",
            AsyncMock(return_value=_CacheHit()),
        ),
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(side_effect=AssertionError("must not run agent")),
        ),
    ):
        started = time.perf_counter()
        first_delta_ms = None
        async for event in chat_service.iter_ask_events(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=anthropic,
            message="what should I focus on today",
            user_key="reviewer-a",
        ):
            if event.get("type") == "delta" and first_delta_ms is None:
                first_delta_ms = (time.perf_counter() - started) * 1000
    assert first_delta_ms is not None
    assert first_delta_ms < 200


@pytest.mark.asyncio
async def test_cold_retrieval_p95_under_500ms_with_llm_mocked() -> None:
    from app.services import chat_service

    hit = SearchHit(
        thread_id=THREAD_A,
        mailbox=SALES,
        conversation_id="conv-a",
        subject="Invoice dispute — overdue billing",
        state="REQUIRES_HUMAN",
        urgency="HIGH",
        snippet="Please review the overdue billing packet.",
        score=0.02,
        last_message_at=datetime(2026, 8, 5, 15, 0, tzinfo=UTC),
    )
    elapsed: list[float] = []
    with (
        patch(
            "app.services.chat_service.embedding_service.embed_text",
            AsyncMock(return_value=NEAR),
        ),
        patch(
            "app.services.chat_service.chat_cache_repo.find_semantic_hit",
            AsyncMock(return_value=None),
        ),
        patch("app.services.chat_service.chat_cache_repo.store", AsyncMock()),
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(return_value=ChatAgentResult(answer="Focus on billing.", hits=[hit])),
        ),
    ):
        for i in range(20):
            started = time.perf_counter()
            await chat_service.ask(
                AsyncMock(),
                _settings(),
                openai_client=MagicMock(),
                anthropic_client=MagicMock(),
                message=f"billing disputes {i}",
                user_key="reviewer-a",
            )
            elapsed.append((time.perf_counter() - started) * 1000)
    elapsed.sort()
    p95 = elapsed[18]
    assert p95 < 500
    assert len(elapsed) == 20
