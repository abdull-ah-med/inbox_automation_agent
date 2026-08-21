"""Chat semantic cache: skip LLM on hit; never store refusals or no-match."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.llm.chat import ChatAgentResult
from app.llm.chat_prompts import NO_MATCH_ANSWER, WRITE_REFUSAL_ANSWER
from app.models.schemas.search import SearchHit, SearchResponse

SALES = "sales@example.com"
THREAD_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER = "user-sub-1"
NEAR = [0.96] + [0.0] * 1535
NEAR[1] = 0.28


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "local",
        "jwt_secret": "c" * 64,
        "frontend_origin": "http://localhost:3000",
        "cookie_secure": False,
        "target_mailboxes": SALES,
        "anthropic_api_key": "sk-ant-test",
        "openai_api_key": "sk-test",
        "chat_model": "claude-haiku-4-5",
        "classification_model": "claude-haiku-4-5",
        "chat_semantic_cache_enabled": True,
        "database_url": (
            "postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test"
        ),
        "redis_url": "redis://localhost:6379/15",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _hit() -> SearchHit:
    return SearchHit(
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


class _CacheHit:
    def __init__(self) -> None:
        self.response_json = {
            "answer": "Focus on Invoice dispute — overdue billing.",
            "citations": [
                {
                    "thread_id": str(THREAD_A),
                    "mailbox": SALES,
                    "subject": "Invoice dispute — overdue billing",
                    "state": "REQUIRES_HUMAN",
                    "urgency": "HIGH",
                    "snippet": "Please review the overdue billing packet.",
                    "url_path": f"/threads/{THREAD_A}",
                }
            ],
            "retrieval_count": 1,
            "mailbox": SALES,
            "refused_write": False,
        }
        self.similarity = 0.96
        self.id = uuid.uuid4()


@pytest.mark.asyncio
async def test_cache_hit_skips_llm_call() -> None:
    from app.services import chat_service

    cached = _CacheHit()
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
            AsyncMock(return_value=cached),
        ),
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(side_effect=AssertionError("cache hit must not run agent")),
        ),
    ):
        result = await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=anthropic,
            message="billing disputes waiting on review",
            user_key=USER,
        )

    assert result.cached is True
    assert result.cache_similarity == pytest.approx(0.96)
    assert result.answer == "Focus on Invoice dispute — overdue billing."
    assert result.citations[0].thread_id == THREAD_A


@pytest.mark.asyncio
async def test_no_match_response_is_never_cached() -> None:
    from app.services import chat_service

    store = AsyncMock()
    with (
        patch(
            "app.services.chat_service.embedding_service.embed_text",
            AsyncMock(return_value=NEAR),
        ),
        patch(
            "app.services.chat_service.chat_cache_repo.find_semantic_hit",
            AsyncMock(return_value=None),
        ),
        patch("app.services.chat_service.chat_cache_repo.store", store),
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(return_value=ChatAgentResult(answer="", hits=[])),
        ),
    ):
        result = await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="zzzxxyy no such vendor",
            user_key=USER,
        )

    assert result.answer != ""
    assert NO_MATCH_ANSWER.split(".")[0] in result.answer or "no matching" in result.answer.lower()
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_refusal_is_never_cached() -> None:
    from app.services import chat_service

    store = AsyncMock()
    with (
        patch(
            "app.services.chat_service.search_service.search_threads",
            AsyncMock(
                return_value=SearchResponse(query="approve this", mailbox=SALES, hits=[])
            ),
        ),
        patch("app.services.chat_service.chat_cache_repo.store", store),
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(side_effect=AssertionError("write intent must not call agent")),
        ),
    ):
        result = await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="approve this and send",
            user_key=USER,
        )

    assert WRITE_REFUSAL_ANSWER in result.answer
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_bypass_cache_skips_lookup_and_store() -> None:
    from app.services import chat_service

    lookup = AsyncMock()
    store = AsyncMock()
    hit = _hit()
    with (
        patch(
            "app.services.chat_service.embedding_service.embed_text",
            AsyncMock(return_value=NEAR),
        ),
        patch("app.services.chat_service.chat_cache_repo.find_semantic_hit", lookup),
        patch("app.services.chat_service.chat_cache_repo.store", store),
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(
                return_value=ChatAgentResult(
                    answer="Focus on Invoice dispute — overdue billing.",
                    hits=[hit],
                )
            ),
        ),
    ):
        result = await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="what should I focus on today",
            user_key=USER,
            bypass_cache=True,
        )

    assert result.cached is False
    lookup.assert_not_awaited()
    store.assert_not_awaited()
    assert "Invoice dispute" in result.answer


@pytest.mark.asyncio
async def test_follow_up_intent_skips_semantic_cache_embed() -> None:
    """Deterministic get_thread asks never cache-hit; skip the OpenAI embed."""
    from app.models.schemas.chat import ChatCitedThread, ChatHistoryTurn
    from app.services import chat_service

    embed = AsyncMock(side_effect=AssertionError("must not embed for follow-up"))
    hit = _hit()
    with (
        patch("app.services.chat_service.embedding_service.embed_text", embed),
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(
                return_value=ChatAgentResult(
                    answer="The overdue billing packet still needs review.",
                    hits=[hit],
                )
            ),
        ),
    ):
        result = await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="tell me more about the first one",
            user_key=USER,
            history=[
                ChatHistoryTurn(role="user", content="billing disputes waiting on review"),
                ChatHistoryTurn(
                    role="assistant",
                    content="The overdue billing dispute is waiting on review.",
                    citations=[
                        ChatCitedThread(thread_id=hit.thread_id, subject=hit.subject),
                    ],
                ),
            ],
        )

    assert result.cached is False
    embed.assert_not_awaited()
    assert "overdue billing" in result.answer.lower()


@pytest.mark.asyncio
async def test_overview_intent_skips_semantic_cache_embed() -> None:
    from app.services import chat_service

    embed = AsyncMock(side_effect=AssertionError("must not embed for overview"))
    with (
        patch("app.services.chat_service.embedding_service.embed_text", embed),
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(
                return_value=ChatAgentResult(
                    answer="There are three threads waiting.",
                    hits=[],
                    grounded=True,
                )
            ),
        ),
    ):
        result = await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="what should I focus on today?",
            user_key=USER,
        )

    assert result.cached is False
    embed.assert_not_awaited()


@pytest.mark.db
@pytest.mark.asyncio
async def test_cache_lookup_missing_table_does_not_block_search(db_session) -> None:
    """Production is still on 029; cache lookup must not abort the ask session."""
    from sqlalchemy import text

    from app.core.exceptions import SearchError
    from app.services import chat_service, search_service

    async def missing_table(*_args, **_kwargs):
        await db_session.execute(text("SELECT 1 FROM chat_response_cache_missing"))

    with (
        patch(
            "app.services.chat_service.embedding_service.embed_text",
            AsyncMock(return_value=NEAR),
        ),
        patch(
            "app.services.chat_service.chat_cache_repo.find_semantic_hit",
            missing_table,
        ),
    ):
        cached, _embedding = await chat_service._lookup_semantic_cache(
            db_session,
            _settings(),
            openai_client=MagicMock(),
            message="give me the latest on omason",
            mailbox=None,
            user_key=USER,
            bypass_cache=False,
        )

    assert cached is None

    try:
        result = await search_service.search_threads(
            db_session,
            _settings(openai_api_key=""),
            openai_client=None,
            query="omason",
            mailbox=None,
            limit=5,
            mode="keyword",
            allow_empty=True,
        )
    except SearchError as exc:
        pytest.fail(f"cache lookup poisoned search: {exc}")

    assert result.hits == []
