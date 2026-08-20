"""Chat agent loop: retrieve via tools, then answer from those hits.

Break if ask pre-searches and canned-no-matches before Claude can call tools.
"""

from __future__ import annotations

import asyncio
import re
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
        "get_overview",
    ]
    assert first["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    second = client.messages.stream.call_args.kwargs
    assert second["tool_choice"] == {"type": "none"}
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
    catalog_turns = []
    for turn in client.messages.create.await_args_list[0].kwargs["messages"]:
        if turn["role"] != "user":
            continue
        content = turn["content"]
        if isinstance(content, str):
            catalog_turns.append(content)
            continue
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    catalog_turns.append(str(block.get("text") or ""))
                elif block.get("type") == "search_result":
                    catalog_turns.append(str(block.get("title") or ""))
                    for item in block.get("content") or []:
                        catalog_turns.append(str(item.get("text") or ""))
    assert any(str(THREAD_A) in content for content in catalog_turns)
    assert any("Invoice dispute" in content for content in catalog_turns)
    assert opened == ["get_thread"]
    assert "packet" in result.answer.lower()
    assert result.hits[0].thread_id == THREAD_A


@pytest.mark.asyncio
async def test_prior_citations_are_packed_as_search_result_blocks() -> None:
    from app.llm.chat import run_chat_agent
    from app.models.schemas.chat import ChatCitedThread
    from app.services.chat_tools import ChatToolExecution

    hit = _hit()
    tool_resp = _tool_use(
        name="get_thread",
        tool_use_id="tu1",
        tool_input={"thread_id": str(THREAD_A)},
    )
    client = MagicMock()
    first_messages: list[dict] = []

    async def create_side_effect(**kwargs):
        if not first_messages:
            for turn in kwargs["messages"]:
                content = turn["content"]
                first_messages.append(
                    {
                        "role": turn["role"],
                        "content": list(content) if isinstance(content, list) else content,
                    }
                )
        return tool_resp

    client.messages.create = AsyncMock(side_effect=create_side_effect)
    client.messages.stream.return_value = _FakeAnswerStream(
        "The packet in Invoice dispute — overdue billing still needs review."
    )

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        _ = name, arguments
        return ChatToolExecution(hits=[hit], status="Opening thread")

    await run_chat_agent(
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
    last_user = [turn for turn in first_messages if turn["role"] == "user"][-1]
    content = last_user["content"]
    assert isinstance(content, list)
    results = [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "search_result"
    ]
    assert len(results) == 1
    assert results[0]["source"] == f"/threads/{THREAD_A}"
    assert results[0]["title"] == "Invoice dispute"
    assert results[0]["citations"] == {"enabled": True}
    assert str(THREAD_A) in results[0]["content"][0]["text"]
    texts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    assert any("tell me more about the first one" in text for text in texts)


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
async def test_agent_emits_status_before_the_tool_returns() -> None:
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
    released = asyncio.Event()

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        _ = name, arguments
        await asyncio.wait_for(released.wait(), timeout=2)
        return ChatToolExecution(hits=[hit], status="Searching mail")

    events: list[dict] = []

    async def consume() -> None:
        async for event in iter_chat_agent(
            client=client,
            settings=_settings(),
            question="billing disputes waiting on review",
            execute_tool=execute,
        ):
            events.append(event)
            if event.get("type") == "status":
                released.set()

    await asyncio.wait_for(consume(), timeout=3)
    assert events[0] == {"type": "status", "text": "Searching mail"}
    assert events[-1]["type"] == "result"


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


@pytest.mark.asyncio
async def test_agent_forces_the_intent_tool_on_the_first_call() -> None:
    from app.llm.chat import run_chat_agent
    from app.services.chat_tools import ChatToolExecution

    hit = _hit()
    client = _client_tool_then_answer(
        _tool_use(
            name="search_mail",
            tool_use_id="tu1",
            tool_input={"query": "Ashley Cantrell"},
        ),
        "Ashley Cantrell asked about a background check.",
    )

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        _ = arguments
        assert name == "search_mail"
        return ChatToolExecution(hits=[hit], status="Searching mail")

    result = await run_chat_agent(
        client=client,
        settings=_settings(),
        question="Ashley Cantrell",
        execute_tool=execute,
        initial_tool="search_mail",
    )
    first = client.messages.create.await_args_list[0].kwargs
    assert first["tool_choice"] == {"type": "tool", "name": "search_mail"}
    assert result.hits[0].thread_id == THREAD_A


@pytest.mark.asyncio
async def test_agent_answers_overview_without_thread_hits() -> None:
    from app.llm.chat import run_chat_agent
    from app.services.chat_tools import ChatToolExecution

    overview = (
        "mailbox: sales@example.com\n"
        "threads: 3\n"
        "awaiting_action: 2\n"
        "stale: 0\n"
        "urgency: HIGH=2 LOW=1"
    )
    client = _client_tool_then_answer(
        _tool_use(name="get_overview", tool_use_id="tu1", tool_input={}),
        "2 threads are waiting on review in sales.",
    )

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        _ = name, arguments
        return ChatToolExecution(
            hits=[],
            status="Summarizing mailbox",
            overview=overview,
        )

    result = await run_chat_agent(
        client=client,
        settings=_settings(),
        question="how many threads are waiting",
        execute_tool=execute,
        initial_tool="get_overview",
    )
    packed = client.messages.stream.call_args.kwargs["messages"][-1]["content"][0]
    assert packed["type"] == "tool_result"
    text = packed["content"][0]["content"][0]["text"]
    assert "awaiting_action: 2" in text
    assert "HIGH=2 LOW=1" in text
    assert text.startswith("<untrusted_content_")
    assert "</untrusted_content_" in text
    assert result.hits == []
    assert result.grounded is True
    assert "2 threads" in result.answer.lower()


def test_fit_chat_messages_drops_oldest_history_first() -> None:
    from app.llm.chat import fit_chat_messages

    messages = [
        {"role": "user", "content": "A" * 50},
        {"role": "assistant", "content": "B" * 50},
        {"role": "user", "content": "CHARLIE-newer"},
        {"role": "assistant", "content": "delta reply"},
        {"role": "user", "content": "Reviewer question:\nlatest on billing"},
    ]
    fitted = fit_chat_messages(messages, budget_chars=120)
    blob = " ".join(
        turn["content"] if isinstance(turn["content"], str) else "" for turn in fitted
    )
    assert "latest on billing" in blob
    assert "CHARLIE-newer" in blob
    assert "A" * 50 not in blob


def test_fit_chat_messages_shrinks_search_result_content_after_history() -> None:
    from app.llm.chat import fit_chat_messages

    huge = "Z" * 400
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Reviewer question:\nlatest"},
                {
                    "type": "search_result",
                    "source": "/dashboard",
                    "title": "Mailbox overview",
                    "content": [{"type": "text", "text": huge}],
                    "citations": {"enabled": True},
                },
            ],
        }
    ]
    fitted = fit_chat_messages(messages, budget_chars=120)
    content = fitted[0]["content"]
    texts = [block["text"] for block in content if block.get("type") == "text"]
    result_text = content[1]["content"][0]["text"]
    assert any("latest" in text for text in texts)
    assert len(result_text) < 400
    assert result_text.startswith("Z")


def test_fit_chat_messages_drops_oldest_when_token_budget_exceeded() -> None:
    from app.llm.chat import fit_chat_messages

    messages = [
        {"role": "user", "content": "oldest turn about ACH forms"},
        {"role": "assistant", "content": "ACH reply"},
        {"role": "user", "content": "latest on billing"},
    ]

    def count_tokens(fitted: list[dict]) -> int:
        return 80 * len(fitted)

    fitted = fit_chat_messages(
        messages,
        budget_tokens=160,
        token_counter=count_tokens,
    )
    blob = " ".join(str(turn["content"]) for turn in fitted)
    assert "latest on billing" in blob
    assert "oldest turn about ACH forms" not in blob


def test_fit_chat_messages_falls_back_to_chars_when_token_counter_fails() -> None:
    from app.llm.chat import fit_chat_messages

    messages = [
        {"role": "user", "content": "A" * 50},
        {"role": "user", "content": "latest on billing"},
    ]

    def boom(_fitted: list[dict]) -> int:
        raise RuntimeError("count_tokens unavailable")

    fitted = fit_chat_messages(
        messages,
        budget_chars=40,
        budget_tokens=10,
        token_counter=boom,
    )
    blob = " ".join(str(turn["content"]) for turn in fitted)
    assert "latest on billing" in blob
    assert "A" * 50 not in blob


@pytest.mark.asyncio
async def test_fit_chat_request_skips_count_tokens_when_under_char_budget() -> None:
    from app.llm.chat import fit_chat_request

    client = MagicMock()
    client.messages.count_tokens = AsyncMock(
        return_value=MagicMock(input_tokens=12)
    )
    messages = [{"role": "user", "content": "billing disputes waiting on review"}]
    fitted = await fit_chat_request(
        client=client,
        request={"model": "claude-haiku-4-5"},
        messages=messages,
        settings=_settings(),
    )
    assert fitted[0]["content"] == "billing disputes waiting on review"
    client.messages.count_tokens.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_does_not_retry_after_streaming_a_partial_answer() -> None:
    import httpx
    from anthropic import APIError

    from app.core.exceptions import ChatError
    from app.llm.chat import iter_chat_agent
    from app.services.chat_tools import ChatToolExecution

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    class _PartialThenError:
        async def __aenter__(self) -> _PartialThenError:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        @property
        def text_stream(self):
            async def _gen():
                yield "The overdue "
                raise APIError(message="overloaded", request=request, body=None)

            return _gen()

        async def get_final_message(self) -> MagicMock:
            raise AssertionError("must not finalize after a stream error")

    hit = _hit()
    client = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=[
            _tool_use(
                name="search_mail",
                tool_use_id="tu1",
                tool_input={"query": "billing disputes"},
            ),
            _tool_use(
                name="search_mail",
                tool_use_id="tu2",
                tool_input={"query": "billing disputes"},
            ),
        ]
    )
    client.messages.stream.return_value = _PartialThenError()

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        _ = name, arguments
        return ChatToolExecution(hits=[hit], status="Searching mail")

    events: list[dict] = []
    with pytest.raises(ChatError, match="Claude chat failed"):
        async for event in iter_chat_agent(
            client=client,
            settings=_settings(),
            question="billing disputes waiting on review",
            execute_tool=execute,
        ):
            events.append(event)

    deltas = [event["text"] for event in events if event.get("type") == "delta"]
    assert deltas == ["The overdue "]


def _system_text(kwargs: dict) -> str:
    system = kwargs.get("system") or []
    if isinstance(system, str):
        return system
    parts: list[str] = []
    for block in system:
        if isinstance(block, dict):
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def _salt_from_system(kwargs: dict) -> str:
    system = _system_text(kwargs)
    match = re.search(r"<untrusted_content_([0-9a-f]{8})>", system)
    assert match is not None
    return f"untrusted_content_{match.group(1)}"


@pytest.mark.asyncio
async def test_delimiter_tag_is_unique_per_request() -> None:
    from app.llm.chat import run_chat_agent
    from app.services.chat_tools import ChatToolExecution

    hit = _hit()

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        _ = name, arguments
        return ChatToolExecution(hits=[hit], status="Searching mail")

    tags: list[str] = []
    for _ in range(2):
        client = _client_tool_then_answer(
            _tool_use(
                name="search_mail",
                tool_use_id="tu1",
                tool_input={"query": "billing disputes"},
            ),
            "The overdue billing packet still needs review.",
        )
        await run_chat_agent(
            client=client,
            settings=_settings(),
            question="billing disputes waiting on review",
            execute_tool=execute,
        )
        tags.append(_salt_from_system(client.messages.create.await_args.kwargs))
    assert tags[0] != tags[1]


@pytest.mark.asyncio
async def test_system_prompt_references_actual_salted_tag() -> None:
    from app.llm.chat import run_chat_agent
    from app.services.chat_tools import ChatToolExecution

    hit = _hit()

    async def execute(name: str, arguments: dict) -> ChatToolExecution:
        _ = name, arguments
        return ChatToolExecution(hits=[hit], status="Searching mail")

    client = _client_tool_then_answer(
        _tool_use(
            name="search_mail",
            tool_use_id="tu1",
            tool_input={"query": "billing disputes"},
        ),
        "The overdue billing packet still needs review.",
    )
    await run_chat_agent(
        client=client,
        settings=_settings(),
        question="billing disputes waiting on review",
        execute_tool=execute,
    )
    kwargs = client.messages.create.await_args.kwargs
    tag = _salt_from_system(kwargs)
    assert f"<{tag}>" in _system_text(kwargs)
    packed = str(client.messages.stream.call_args.kwargs.get("messages"))
    assert f"<{tag}>" in packed
    assert f"</{tag}>" in packed
    assert "overdue billing packet" in packed
