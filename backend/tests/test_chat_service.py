"""Chat ask service: grounded citations, no-hit path, write-intent, mailbox scope."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import ChatError
from app.llm.chat_prompts import (
    CHAT_SYSTEM_PROMPT,
    NO_MATCH_ANSWER,
    UNTRUSTED_RETRIEVED_TAG,
    WRITE_REFUSAL_ANSWER,
)
from app.llm.prompts import wrap_untrusted
from app.models.schemas.search import SearchHit, SearchResponse
from app.services.chat_service import (
    citations_from_hits,
    detect_write_intent,
    thread_url_path,
)

SALES = "sales@example.com"
CR = "cr@example.com"
THREAD_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
THREAD_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
MANGLED_ASHLEY_ID = "bbbbbbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
INVENTED_ID = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "local",
        "jwt_secret": "c" * 64,
        "frontend_origin": "http://localhost:3000",
        "cookie_secure": False,
        "target_mailboxes": f"{SALES},{CR}",
        "anthropic_api_key": "sk-ant-test",
        "chat_model": "claude-haiku-4-5",
        "classification_model": "claude-haiku-4-5",
        "database_url": ("postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test"),
        "redis_url": "redis://localhost:6379/15",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _hit(
    thread_id: uuid.UUID = THREAD_A,
    *,
    mailbox: str = SALES,
    subject: str = "Invoice dispute — overdue billing",
    snippet: str = "Invoice dispute — Please review the overdue billing packet.",
    state: str = "REQUIRES_HUMAN",
    urgency: str = "HIGH",
) -> SearchHit:
    return SearchHit(
        thread_id=thread_id,
        mailbox=mailbox,
        conversation_id=f"conv-{thread_id}",
        subject=subject,
        state=state,
        urgency=urgency,
        snippet=snippet,
        score=0.02,
        last_message_at=datetime(2026, 8, 5, 15, 0, tzinfo=UTC),
    )


def _search_response(*hits: SearchHit, mailbox: str | None = None) -> SearchResponse:
    return SearchResponse(query="billing disputes", mailbox=mailbox, hits=list(hits))


def test_detect_write_intent_flags_approve_and_send() -> None:
    assert detect_write_intent("approve this and send") is True
    assert detect_write_intent("Please send the draft") is True
    assert detect_write_intent("move this to junk") is True
    assert detect_write_intent("delete the thread") is True


def test_detect_write_intent_ignores_read_only_asks() -> None:
    assert detect_write_intent("billing disputes waiting on review") is False
    assert detect_write_intent("what did we send last week?") is False
    assert detect_write_intent("show me threads about SampleLab") is False


def test_thread_url_path_is_frontend_relative() -> None:
    assert thread_url_path(THREAD_A) == f"/threads/{THREAD_A}"


def test_citations_always_come_from_retrieval_hits() -> None:
    hits = [
        _hit(THREAD_A, mailbox=SALES, subject="Invoice A"),
        _hit(THREAD_B, mailbox=CR, subject="Invoice B"),
    ]
    citations = citations_from_hits(hits)
    assert [c.thread_id for c in citations] == [THREAD_A, THREAD_B]
    assert citations[0].url_path == f"/threads/{THREAD_A}"
    assert citations[0].mailbox == SALES
    assert citations[0].subject == "Invoice A"
    assert citations[0].state == "REQUIRES_HUMAN"
    assert citations[0].urgency == "HIGH"
    assert citations[0].snippet is not None
    assert "overdue billing" in citations[0].snippet


def test_retrieved_context_wraps_hits_in_untrusted_delimiters() -> None:
    from app.llm.chat import build_retrieved_context

    hit = _hit(
        snippet="Please wire 123-45-6789 by Friday.",
        subject="SSN in body",
    )
    packed = build_retrieved_context([hit], question="billing disputes")
    wrapped = wrap_untrusted(
        UNTRUSTED_RETRIEVED_TAG,
        "placeholder",
    )
    open_tag = f"<{UNTRUSTED_RETRIEVED_TAG}>"
    close_tag = f"</{UNTRUSTED_RETRIEVED_TAG}>"
    assert open_tag in packed
    assert close_tag in packed
    assert packed.count(open_tag) == 1
    assert packed.count(close_tag) == 1
    assert str(THREAD_A) not in packed
    assert SALES in packed
    assert "SSN in body" in packed
    assert "REQUIRES_HUMAN" in packed
    assert "HIGH" in packed
    assert "billing disputes" in packed
    # Question lives outside the untrusted block.
    question_idx = packed.index("billing disputes")
    assert question_idx < packed.index(open_tag) or question_idx > packed.rindex(close_tag)
    assert wrapped.startswith(open_tag)


def test_retrieved_context_scrubs_pii_inside_untrusted_block() -> None:
    from app.llm.chat import build_retrieved_context

    hit = _hit(snippet="SSN 123-45-6789 is on the form.")
    packed = build_retrieved_context([hit], question="ssn thread")
    open_tag = f"<{UNTRUSTED_RETRIEVED_TAG}>"
    close_tag = f"</{UNTRUSTED_RETRIEVED_TAG}>"
    inner = packed[packed.index(open_tag) : packed.index(close_tag) + len(close_tag)]
    assert "123-45-6789" not in inner
    assert "[REDACTED_SSN]" in inner


def test_chat_system_prompt_is_read_only_and_grounded() -> None:
    lowered = CHAT_SYSTEM_PROMPT.lower()
    assert "read-only" in lowered
    assert "untrusted" in lowered
    assert "never invent" in lowered or "do not invent" in lowered
    assert "never follow instructions" in lowered
    assert "approve" in lowered
    assert "send" in lowered
    # Cite by subject; UUIDs belong on citation cards, not in the answer text.
    assert "uuid" in lowered
    assert "subject" in lowered
    assert "review ui" not in lowered
    assert "triage labels" in lowered
    assert "overview" in lowered or "latest" in lowered


def test_retrieved_context_includes_last_message_at() -> None:
    from app.llm.chat import build_retrieved_context

    packed = build_retrieved_context([_hit()], question="what should I focus on today?")
    assert "2026-08-05T15:00:00+00:00" in packed


@pytest.mark.asyncio
async def test_ask_returns_server_citations_from_search_hits_not_model_ids() -> None:
    from app.llm.chat import ChatAgentResult
    from app.services import chat_service

    hit = _hit(THREAD_A)
    fake_id = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    session = AsyncMock()
    settings = _settings()
    openai = MagicMock()
    anthropic = MagicMock()

    with patch(
        "app.services.chat_service.run_chat_agent",
        AsyncMock(
            return_value=ChatAgentResult(
                answer=(f"Open thread {fake_id} about billing. See {hit.subject} ({THREAD_A})."),
                hits=[hit],
            )
        ),
    ) as agent_mock:
        result = await chat_service.ask(
            session,
            settings,
            openai_client=openai,
            anthropic_client=anthropic,
            message="billing disputes waiting on review",
            mailbox=SALES,
            limit=10,
        )

    agent_mock.assert_awaited_once()
    assert result.refused_write is False
    assert result.retrieval_count == 1
    assert [c.thread_id for c in result.citations] == [THREAD_A]
    assert fake_id not in {c.thread_id for c in result.citations}
    assert result.citations[0].url_path == f"/threads/{THREAD_A}"
    assert str(fake_id) not in result.answer
    assert str(THREAD_A) not in result.answer


def test_sanitize_chat_answer_strips_mangled_and_valid_thread_ids() -> None:
    """Eval Ashley case printed a 12-4-4-4-12 id that is not the retrieved UUID."""
    from app.llm.chat import sanitize_chat_answer

    raw = (
        "I found 1 thread related to Ashley Cantrell:\n\n"
        f'**Thread: "Background check inquiry"** (ID: {MANGLED_ASHLEY_ID})\n'
        f"- thread id: {THREAD_A}\n"
        f"See {INVENTED_ID} for billing."
    )
    cleaned = sanitize_chat_answer(raw)
    assert MANGLED_ASHLEY_ID not in cleaned
    assert str(THREAD_A) not in cleaned
    assert str(INVENTED_ID) not in cleaned
    assert "Ashley Cantrell" in cleaned
    assert "Background check inquiry" in cleaned
    assert "ID:" not in cleaned
    assert "thread id:" not in cleaned.lower()


def test_sanitize_chat_answer_leaves_grounded_prose() -> None:
    from app.llm.chat import sanitize_chat_answer

    prose = (
        "Ashley Cantrell with Stronger Together asked about a background check "
        "in Background check inquiry."
    )
    assert sanitize_chat_answer(prose) == prose


@pytest.mark.asyncio
async def test_ask_no_hits_returns_empty_citations_without_invented_answer() -> None:
    from app.llm.chat import ChatAgentResult
    from app.services import chat_service

    session = AsyncMock()
    settings = _settings()
    with patch(
        "app.services.chat_service.run_chat_agent",
        AsyncMock(
            return_value=ChatAgentResult(
                answer="I invented a thread that is not in the inbox.",
                hits=[],
            )
        ),
    ):
        result = await chat_service.ask(
            session,
            settings,
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="zzzxxyyq no such thread",
        )

    assert result.citations == []
    assert result.retrieval_count == 0
    assert result.refused_write is False
    assert result.answer == NO_MATCH_ANSWER


@pytest.mark.asyncio
async def test_ask_write_intent_refuses_without_mutation_and_still_retrieves() -> None:
    from app.services import chat_service

    hit = _hit(THREAD_A)
    session = AsyncMock()
    settings = _settings()
    with (
        patch(
            "app.services.chat_service.search_service.search_threads",
            AsyncMock(return_value=_search_response(hit, mailbox=SALES)),
        ) as search_mock,
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(side_effect=AssertionError("write-intent must not call Claude")),
        ),
        patch("app.services.draft_feedback_service.approve_draft", AsyncMock()) as approve,
        patch("app.services.draft_feedback_service.reject_draft", AsyncMock()) as reject,
    ):
        result = await chat_service.ask(
            session,
            settings,
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="approve this and send",
            mailbox=SALES,
        )

    search_mock.assert_awaited_once()
    approve.assert_not_called()
    reject.assert_not_called()
    assert result.refused_write is True
    assert result.retrieval_count == 1
    assert [c.thread_id for c in result.citations] == [THREAD_A]
    assert result.citations[0].url_path == f"/threads/{THREAD_A}"
    assert WRITE_REFUSAL_ANSWER in result.answer or "review" in result.answer.lower()
    assert "open" in result.answer.lower()


@pytest.mark.asyncio
async def test_ask_passes_mailbox_filter_through_to_agent() -> None:
    from app.llm.chat import ChatAgentResult
    from app.services import chat_service

    session = AsyncMock()
    settings = _settings()
    with patch(
        "app.services.chat_service.run_chat_agent",
        AsyncMock(return_value=ChatAgentResult(answer="", hits=[])),
    ) as agent_mock:
        result = await chat_service.ask(
            session,
            settings,
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="threads about SampleLab",
            mailbox=CR,
            limit=5,
        )

    assert result.mailbox == CR
    assert result.citations == []
    agent_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_ask_maps_llm_failure_to_chat_error() -> None:
    from app.services import chat_service

    session = AsyncMock()
    settings = _settings()
    with (
        patch(
            "app.services.chat_service.search_service.search_threads",
            AsyncMock(return_value=_search_response(_hit())),
        ),
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(side_effect=ChatError("Claude chat failed")),
        ),
        pytest.raises(ChatError, match="Claude chat failed"),
    ):
        await chat_service.ask(
            session,
            settings,
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="billing disputes",
        )


@pytest.mark.asyncio
async def test_ask_uses_agent_hits_even_if_keyword_search_would_miss() -> None:
    from app.llm.chat import ChatAgentResult
    from app.services import chat_service

    hit = _hit(THREAD_A)
    session = AsyncMock()
    settings = _settings()
    with (
        patch(
            "app.services.chat_service.search_service.search_threads",
            AsyncMock(return_value=_search_response()),
        ),
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(
                return_value=ChatAgentResult(
                    answer="Focus on Invoice dispute — overdue billing.",
                    hits=[hit],
                )
            ),
        ) as agent_mock,
    ):
        result = await chat_service.ask(
            session,
            settings,
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="what should I focus on today?",
        )

    agent_mock.assert_awaited_once()
    assert "Invoice dispute" in result.answer
    assert result.retrieval_count == 1
    assert [c.thread_id for c in result.citations] == [THREAD_A]


@pytest.mark.asyncio
async def test_ask_follow_up_passes_cited_threads_to_the_agent() -> None:
    from app.llm.chat import ChatAgentResult
    from app.models.schemas.chat import ChatCitedThread, ChatHistoryTurn
    from app.services import chat_service

    hit = _hit(THREAD_A)
    session = AsyncMock()
    settings = _settings()
    history = [
        ChatHistoryTurn(role="user", content="billing disputes waiting on review"),
        ChatHistoryTurn(
            role="assistant",
            content="The overdue billing dispute is waiting on review.",
            citations=[ChatCitedThread(thread_id=THREAD_A, subject="Invoice dispute")],
        ),
    ]
    with patch(
        "app.services.chat_service.run_chat_agent",
        AsyncMock(
            return_value=ChatAgentResult(
                answer="The packet is still waiting on review.",
                hits=[hit],
            )
        ),
    ) as agent_mock:
        result = await chat_service.ask(
            session,
            settings,
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="tell me more",
            history=history,
        )

    passed = agent_mock.await_args.kwargs["history"]
    assert passed[-1].citations[0].thread_id == THREAD_A
    assert agent_mock.await_args.kwargs["question"] == "tell me more"
    assert result.answer == "The packet is still waiting on review."
