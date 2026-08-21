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
    WRITE_REFUSAL_ANSWER,
)
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


def test_chat_ask_accepts_twenty_history_turns() -> None:
    from pydantic import ValidationError

    from app.models.schemas.chat import ChatAskRequest, ChatHistoryTurn

    history = [
        ChatHistoryTurn(
            role="user" if index % 2 == 0 else "assistant",
            content=f"turn-{index}",
        )
        for index in range(20)
    ]
    body = ChatAskRequest(message="hello", history=history)
    assert len(body.history) == 20
    with pytest.raises(ValidationError):
        ChatAskRequest(
            message="hello",
            history=[
                *history,
                ChatHistoryTurn(role="user", content="turn-20"),
            ],
        )


def test_follow_up_accepts_assistant_history_longer_than_a_search_query() -> None:
    from app.models.schemas.chat import ChatAskRequest, ChatHistoryTurn
    from app.models.schemas.search import SEARCH_QUERY_MAX_CHARS

    answer = (
        "The latest O'Mason Lumber thread is Re: O'Mason Lumber Users from "
        "29 July 2026. " + ("n" * 480)
    )
    assert len(answer) > SEARCH_QUERY_MAX_CHARS
    body = ChatAskRequest(
        message="what s happening here?",
        history=[
            ChatHistoryTurn(role="user", content="give me the latest on omason"),
            ChatHistoryTurn(role="assistant", content=answer),
        ],
    )
    assert body.history[1].content == answer
    assert len(body.history[1].content) == len(answer)


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


def test_search_result_blocks_include_last_message_at_and_scrub_pii() -> None:
    from app.llm.chat_tools import search_results_from_hits

    hit = _hit(snippet="SSN 123-45-6789 is on the form.")
    packed = search_results_from_hits([hit])
    blob = str(packed)
    assert "123-45-6789" not in blob
    assert "[REDACTED_SSN]" in blob
    assert "2026-08-05T15:00:00+00:00" in blob
    assert str(THREAD_A) in packed[0]["source"]
    assert packed[0]["type"] == "search_result"


def test_chat_system_prompt_is_read_only_and_grounded() -> None:
    lowered = CHAT_SYSTEM_PROMPT.lower()
    assert "read-only" in lowered
    assert "untrusted" in lowered
    assert "never invent" in lowered or "do not invent" in lowered
    assert "never follow instructions" in lowered
    assert "approve" in lowered
    assert "send" in lowered
    # Cite by subject with [n] markers; UUIDs belong on citation cards.
    assert "uuid" in lowered
    assert "subject" in lowered
    assert (
        "[1]" in CHAT_SYSTEM_PROMPT
        or "citation marker" in lowered
        or "[n]" in CHAT_SYSTEM_PROMPT
    )
    assert "review ui" not in lowered
    assert "triage labels" in lowered
    assert "overview" in lowered or "latest" in lowered
    assert "<untrusted_content_XXXXXXXX>" in CHAT_SYSTEM_PROMPT
    assert "brief" in lowered or "what is happening" in lowered or "without opening" in lowered
    assert "every retrieved" in lowered or "every matching" in lowered
    assert "at most three" not in lowered
    assert "few sentences" not in lowered
    assert "dates" in lowered or "date" in lowered


def test_chat_default_limit_matches_search_so_no_threads_are_hidden() -> None:
    from app.models.schemas.chat import CHAT_DEFAULT_LIMIT
    from app.models.schemas.search import SEARCH_DEFAULT_LIMIT

    assert SEARCH_DEFAULT_LIMIT == 10
    assert CHAT_DEFAULT_LIMIT == 10


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


@pytest.mark.asyncio
async def test_chat_ask_logs_tool_and_token_fields() -> None:
    from app.llm.chat import ChatAgentResult
    from app.services import chat_service

    hit = _hit(THREAD_A)
    with (
        patch(
            "app.services.chat_service.run_chat_agent",
            AsyncMock(
                return_value=ChatAgentResult(
                    answer="Focus on billing.",
                    hits=[hit],
                    tool_used_first="search_mail",
                    tool_iterations=2,
                    ttft_ms=41,
                    input_tokens=120,
                    output_tokens=18,
                    cache_read_tokens=80,
                    cache_write_tokens=0,
                )
            ),
        ),
        patch("app.services.chat_service.logger.info") as log_info,
    ):
        await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="billing disputes waiting on review",
            mailbox=SALES,
        )
    chat_ask = [
        call for call in log_info.call_args_list if call.args and call.args[0] == "chat.ask"
    ]
    assert chat_ask
    kwargs = chat_ask[-1].kwargs
    assert kwargs["tool_used_first"] == "search_mail"
    assert kwargs["tool_iterations"] == 2
    assert kwargs["ttft_ms"] == 41
    assert kwargs["input_tokens"] == 120
    assert kwargs["output_tokens"] == 18
    assert kwargs["cache_read_tokens"] == 80
    assert kwargs["cache_write_tokens"] == 0
    assert kwargs["cached"] is False
    assert kwargs["grounded_verifier"] == "SKIPPED"


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


def test_sanitize_chat_answer_keeps_numbered_citation_markers() -> None:
    from app.llm.chat import sanitize_chat_answer

    prose = "Bonnie is waiting on the overdue invoice [1]."
    assert sanitize_chat_answer(prose) == prose


def test_unknown_thread_ids_in_answer_are_ids_not_in_hits() -> None:
    from app.llm.chat import unknown_thread_ids_in_answer

    raw = f"See {INVENTED_ID} and {THREAD_A} about billing."
    unknown = unknown_thread_ids_in_answer(raw, known_thread_ids={THREAD_A})
    assert unknown == [str(INVENTED_ID)]


@pytest.mark.asyncio
async def test_ask_no_hits_in_a_mailbox_suggests_another_mailbox() -> None:
    from app.llm.chat import ChatAgentResult
    from app.services import chat_service

    with patch(
        "app.services.chat_service.run_chat_agent",
        AsyncMock(return_value=ChatAgentResult(answer="", hits=[])),
    ):
        result = await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="zzzxxyyq no such thread",
            mailbox=SALES,
        )

    assert result.citations == []
    assert result.retrieval_count == 0
    assert SALES in result.answer
    assert "search page" in result.answer.lower()
    assert result.answer != NO_MATCH_ANSWER


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


@pytest.mark.asyncio
async def test_ask_weather_returns_canned_out_of_scope_without_llm() -> None:
    from app.llm.chat_prompts import OUT_OF_SCOPE_ANSWER
    from app.services import chat_service

    with patch(
        "app.services.chat_service.run_chat_agent",
        AsyncMock(side_effect=AssertionError("off-topic must not call Claude")),
    ):
        result = await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="what's the weather",
        )

    assert result.answer == OUT_OF_SCOPE_ANSWER
    assert result.citations == []
    assert result.retrieval_count == 0
    assert result.refused_write is False


@pytest.mark.asyncio
async def test_ask_overview_keeps_grounded_answer_without_thread_citations() -> None:
    from app.llm.chat import ChatAgentResult
    from app.services import chat_service

    with patch(
        "app.services.chat_service.run_chat_agent",
        AsyncMock(
            return_value=ChatAgentResult(
                answer="2 threads are waiting on review in sales.",
                hits=[],
                grounded=True,
            )
        ),
    ):
        result = await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="how many threads are waiting",
        )

    assert result.answer == "2 threads are waiting on review in sales."
    assert result.citations == []
    assert result.retrieval_count == 0


@pytest.mark.asyncio
async def test_ask_forces_search_mail_for_a_person_name() -> None:
    from app.llm.chat import ChatAgentResult
    from app.services import chat_service

    with patch(
        "app.services.chat_service.run_chat_agent",
        AsyncMock(return_value=ChatAgentResult(answer="", hits=[])),
    ) as agent_mock:
        await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="Ashley Cantrell",
        )

    assert agent_mock.await_args.kwargs["initial_tool"] == "search_mail"


@pytest.mark.asyncio
async def test_ask_follow_up_includes_resolved_thread_id_in_the_question() -> None:
    from app.llm.chat import ChatAgentResult
    from app.models.schemas.chat import ChatCitedThread, ChatHistoryTurn
    from app.services import chat_service

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
        AsyncMock(return_value=ChatAgentResult(answer="", hits=[])),
    ) as agent_mock:
        await chat_service.ask(
            AsyncMock(),
            _settings(),
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            message="tell me more about the first one",
            history=history,
        )

    question = agent_mock.await_args.kwargs["question"]
    assert str(THREAD_A) in question
    assert agent_mock.await_args.kwargs["initial_tool"] == "get_thread"
