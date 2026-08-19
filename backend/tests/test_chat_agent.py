"""Chat agent loop: retrieve via tools, then answer from those hits.

Break if ask pre-searches and canned-no-matches before Claude can call tools.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.models.schemas.chat import ChatHistoryTurn
from app.models.schemas.search import SearchHit

SALES = "sales@example.com"
THREAD_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=SALES,
        anthropic_api_key="sk-ant-test",
        chat_model="claude-haiku-4-5",
        classification_model="claude-haiku-4-5",
        database_url=("postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test"),
        redis_url="redis://localhost:6379/15",
    )


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


def _tool_use(*, name: str, tool_use_id: str, tool_input: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_use_id
    block.name = name
    block.input = tool_input
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    resp.usage = None
    return resp


def _text_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    resp.usage = None
    return resp


class _FakeAnswerStream:
    def __init__(self, text: str) -> None:
        self._text = text

    async def __aenter__(self) -> _FakeAnswerStream:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    @property
    def text_stream(self):
        async def _gen():
            yield self._text

        return _gen()

    async def get_final_message(self) -> MagicMock:
        return _text_response(self._text)


def _client_tool_then_answer(tool_resp: MagicMock, answer: str) -> MagicMock:
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=[tool_resp])
    client.messages.stream.return_value = _FakeAnswerStream(answer)
    return client


@pytest.mark.asyncio
async def test_agent_answers_from_search_mail_hits() -> None:
    from app.llm.chat import run_chat_agent
    from app.services.chat_tools import ChatToolExecution

    hit = _hit()
    answer = (
        "The Invoice dispute — overdue billing thread asks to review the overdue billing packet."
    )
    client = _client_tool_then_answer(
        _tool_use(
            name="search_mail",
            tool_use_id="tu1",
            tool_input={"query": "billing disputes"},
        ),
        answer,
    )

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        assert name == "search_mail"
        assert arguments["query"] == "billing disputes"
        return ChatToolExecution(hits=[hit], status="Searching mail")

    result = await run_chat_agent(
        client=client,
        settings=_settings(),
        question="billing disputes waiting on review",
        execute_tool=execute,
    )
    assert result.hits[0].thread_id == THREAD_A
    assert "overdue billing packet" in result.answer.lower()
    assert str(THREAD_A) not in result.answer
    first = client.messages.create.await_args_list[0].kwargs
    assert first["tool_choice"] == {"type": "any"}
    assert first["cache_control"] == {"type": "ephemeral"}
    tools = first["tools"]
    assert [tool["name"] for tool in tools] == [
        "search_mail",
        "list_recent_threads",
        "get_thread",
    ]
    assert first["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    second = client.messages.stream.call_args.kwargs
    assert second["tool_choice"] == {"type": "auto"}
    assert second["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_agent_opens_cited_thread_on_follow_up() -> None:
    from app.llm.chat import run_chat_agent
    from app.models.schemas.chat import ChatCitedThread
    from app.services.chat_tools import ChatToolExecution

    hit = _hit()
    client = _client_tool_then_answer(
        _tool_use(
            name="get_thread",
            tool_use_id="tu1",
            tool_input={"thread_id": str(THREAD_A)},
        ),
        "The packet in Invoice dispute — overdue billing still needs review.",
    )
    opened: list[str] = []

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        opened.append(name)
        assert arguments["thread_id"] == str(THREAD_A)
        return ChatToolExecution(hits=[hit], status="Opening thread")

    result = await run_chat_agent(
        client=client,
        settings=_settings(),
        question="tell me more about the first one",
        execute_tool=execute,
        history=[
            ChatHistoryTurn(role="user", content="billing disputes waiting on review"),
            ChatHistoryTurn(
                role="assistant",
                content="The overdue billing dispute is waiting on review.",
                citations=[ChatCitedThread(thread_id=THREAD_A, subject="Invoice dispute")],
            ),
        ],
    )
    catalog_turns = [
        turn["content"]
        for turn in client.messages.create.await_args_list[0].kwargs["messages"]
        if turn["role"] == "user" and isinstance(turn["content"], str)
    ]
    assert any(str(THREAD_A) in content for content in catalog_turns)
    assert any("Invoice dispute" in content for content in catalog_turns)
    assert opened == ["get_thread"]
    assert "packet" in result.answer.lower()
    assert result.hits[0].thread_id == THREAD_A


@pytest.mark.asyncio
async def test_agent_empty_hits_raise_no_answer_text() -> None:
    from app.llm.chat import run_chat_agent
    from app.services.chat_tools import ChatToolExecution

    client = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=[
            _tool_use(
                name="search_mail",
                tool_use_id="tu1",
                tool_input={"query": "zzzxxyyq"},
            ),
            _text_response("I invented a thread that is not in the inbox."),
        ]
    )

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        _ = name, arguments
        return ChatToolExecution(hits=[], status="Searching mail")

    result = await run_chat_agent(
        client=client,
        settings=_settings(),
        question="zzzxxyyq no such thread",
        execute_tool=execute,
    )
    assert result.hits == []
    assert result.answer == ""


@pytest.mark.asyncio
async def test_agent_emits_searching_mail_status_before_the_answer() -> None:
    from app.llm.chat import iter_chat_agent
    from app.services.chat_tools import ChatToolExecution

    hit = _hit()
    client = _client_tool_then_answer(
        _tool_use(
            name="search_mail",
            tool_use_id="tu1",
            tool_input={"query": "billing disputes"},
        ),
        "The overdue billing packet still needs review.",
    )

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        _ = name, arguments
        return ChatToolExecution(hits=[hit], status="Searching mail")

    events = [
        event
        async for event in iter_chat_agent(
            client=client,
            settings=_settings(),
            question="billing disputes waiting on review",
            execute_tool=execute,
        )
    ]
    assert events[0] == {"type": "status", "text": "Searching mail"}
    assert events[-1]["type"] == "result"
    assert "overdue billing packet" in events[-1]["answer"].lower()
    assert events[-1]["hits"][0].thread_id == THREAD_A
    deltas = [event["text"] for event in events if event["type"] == "delta"]
    assert "".join(deltas) == "The overdue billing packet still needs review."


@pytest.mark.asyncio
async def test_agent_runs_independent_tools_concurrently() -> None:
    from app.llm.chat import run_chat_agent
    from app.services.chat_tools import ChatToolExecution

    hit = _hit()
    windows: list[tuple[str, float, float]] = []
    first = MagicMock()
    first.type = "tool_use"
    first.id = "tu1"
    first.name = "search_mail"
    first.input = {"query": "billing"}
    second = MagicMock()
    second.type = "tool_use"
    second.id = "tu2"
    second.name = "list_recent_threads"
    second.input = {}
    tool_resp = MagicMock()
    tool_resp.stop_reason = "tool_use"
    tool_resp.content = [first, second]
    tool_resp.usage = None
    client = _client_tool_then_answer(
        tool_resp,
        "The overdue billing packet still needs review.",
    )

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        _ = arguments
        started = asyncio.get_running_loop().time()
        await asyncio.sleep(0.08)
        windows.append((name, started, asyncio.get_running_loop().time()))
        return ChatToolExecution(hits=[hit], status=f"Running {name}")

    result = await run_chat_agent(
        client=client,
        settings=_settings(),
        question="billing disputes waiting on review",
        execute_tool=execute,
    )
    assert {name for name, _s, _e in windows} == {"search_mail", "list_recent_threads"}
    (_n1, start_one, end_one), (_n2, start_two, end_two) = windows
    assert start_two < end_one
    assert start_one < end_two
    assert result.hits[0].thread_id == THREAD_A
