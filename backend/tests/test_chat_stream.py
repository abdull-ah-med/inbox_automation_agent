"""Streaming chat: token deltas from Haiku, then SSE to the reviewer."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_user
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.models.schemas.search import SearchHit

SALES = "sales@example.com"
THREAD_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CHUNK_ONE = "The overdue "
CHUNK_TWO = "billing dispute is waiting on review."
FULL_ANSWER = "The overdue billing dispute is waiting on review."


class _FakeStream:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    @property
    def text_stream(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk

        return _gen()


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes="sales@example.com,cr@example.com",
        anthropic_api_key="sk-ant-test",
        chat_model="claude-haiku-4-5",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def _hit() -> SearchHit:
    return SearchHit(
        thread_id=THREAD_A,
        mailbox=SALES,
        conversation_id=f"conv-{THREAD_A}",
        subject="Invoice dispute — overdue billing",
        state="REQUIRES_HUMAN",
        urgency="HIGH",
        snippet="Please review the overdue billing packet.",
        score=0.02,
        last_message_at=datetime(2026, 8, 5, 15, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_stream_chat_answer_yields_token_chunks_in_order() -> None:
    from app.llm.chat import stream_chat_answer

    client = MagicMock()
    client.messages.stream.return_value = _FakeStream([CHUNK_ONE, CHUNK_TWO])
    chunks = [
        piece
        async for piece in stream_chat_answer(
            client=client,
            settings=_settings(),
            question="billing disputes waiting on review",
            hits=[_hit()],
        )
    ]
    assert chunks == [CHUNK_ONE, CHUNK_TWO]
    kwargs = client.messages.stream.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["messages"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_stream_chat_answer_puts_history_before_retrieved_context() -> None:
    from app.llm.chat import stream_chat_answer
    from app.models.schemas.chat import ChatHistoryTurn

    client = MagicMock()
    client.messages.stream.return_value = _FakeStream([CHUNK_ONE, CHUNK_TWO])
    history = [
        ChatHistoryTurn(role="user", content="billing disputes waiting on review"),
        ChatHistoryTurn(
            role="assistant",
            content="The overdue billing dispute is waiting on review.",
        ),
    ]
    chunks = [
        piece
        async for piece in stream_chat_answer(
            client=client,
            settings=_settings(),
            question="tell me more",
            hits=[_hit()],
            history=history,
        )
    ]
    assert chunks == [CHUNK_ONE, CHUNK_TWO]
    messages = client.messages.stream.call_args.kwargs["messages"]
    assert messages[0] == {
        "role": "user",
        "content": "billing disputes waiting on review",
    }
    assert messages[1] == {
        "role": "assistant",
        "content": "The overdue billing dispute is waiting on review.",
    }
    assert messages[2]["role"] == "user"
    assert "tell me more" in messages[2]["content"]
    assert "Invoice dispute" in messages[2]["content"]


async def _agent_events(*events: dict):
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_iter_ask_events_sends_status_then_meta_then_answer_then_done() -> None:
    from app.services import chat_service

    with patch(
        "app.services.chat_service.iter_chat_agent",
        new=lambda **_kwargs: _agent_events(
            {"type": "status", "text": "Searching mail"},
            {"type": "retrieved", "hits": [_hit()]},
            {"type": "delta", "text": CHUNK_ONE},
            {"type": "delta", "text": CHUNK_TWO},
            {"type": "result", "answer": FULL_ANSWER, "hits": [_hit()]},
        ),
    ):
        events = [
            event
            async for event in chat_service.iter_ask_events(
                AsyncMock(),
                _settings(),
                openai_client=MagicMock(),
                anthropic_client=MagicMock(),
                message="billing disputes waiting on review",
                mailbox=SALES,
            )
        ]

    assert events[0] == {"type": "status", "text": "Searching mail"}
    assert events[1]["type"] == "meta"
    assert events[1]["refused_write"] is False
    assert events[1]["retrieval_count"] == 1
    assert events[1]["mailbox"] == SALES
    assert events[1]["citations"][0]["thread_id"] == str(THREAD_A)
    assert events[1]["citations"][0]["url_path"] == f"/threads/{THREAD_A}"
    deltas = [event["text"] for event in events if event["type"] == "delta"]
    assert deltas == [CHUNK_ONE, CHUNK_TWO]
    assert events[-1] == {"type": "done"}


@pytest.mark.asyncio
async def test_iter_ask_events_strips_thread_ids_from_the_agent_answer() -> None:
    """Haiku printed a mangled UUID; the reviewer must not see it."""
    from app.services import chat_service

    mangled = "bbbbbbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    with patch(
        "app.services.chat_service.iter_chat_agent",
        new=lambda **_kwargs: _agent_events(
            {
                "type": "result",
                "answer": (f'Ashley Cantrell is in "Background check inquiry" (ID: {mangled}).'),
                "hits": [_hit()],
            },
        ),
    ):
        events = [
            event
            async for event in chat_service.iter_ask_events(
                AsyncMock(),
                _settings(),
                openai_client=MagicMock(),
                anthropic_client=MagicMock(),
                message="What's happening on Ashley Cantrell, give me the latest",
            )
        ]

    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert mangled not in text
    assert str(THREAD_A) not in text
    assert "Ashley Cantrell" in text
    assert "Background check inquiry" in text


@pytest.mark.asyncio
async def test_iter_ask_events_no_hits_streams_canned_answer_not_invented_text() -> None:
    from app.llm.chat_prompts import NO_MATCH_ANSWER
    from app.services import chat_service

    with patch(
        "app.services.chat_service.iter_chat_agent",
        new=lambda **_kwargs: _agent_events(
            {
                "type": "result",
                "answer": "I invented a thread that is not in the inbox.",
                "hits": [],
            },
        ),
    ):
        events = [
            event
            async for event in chat_service.iter_ask_events(
                AsyncMock(),
                _settings(),
                openai_client=MagicMock(),
                anthropic_client=MagicMock(),
                message="zzzxxyyq no such thread",
            )
        ]

    assert events[0]["type"] == "meta"
    assert events[0]["citations"] == []
    assert events[0]["retrieval_count"] == 0
    assert events[1] == {"type": "delta", "text": NO_MATCH_ANSWER}
    assert events[-1] == {"type": "done"}


@pytest.fixture
def app():
    local_settings = _settings()
    get_settings.cache_clear()
    with (
        patch("app.main.get_settings", return_value=local_settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        application = create_app()
        application.dependency_overrides[get_settings] = lambda: local_settings

        async def fake_user() -> UserMe:
            return UserMe(
                id=uuid.uuid4(),
                email="elise@example.com",
                role="user",
                created_at=datetime.now(UTC),
            )

        application.dependency_overrides[get_current_user] = fake_user

        async def fake_db():
            yield AsyncMock()

        application.dependency_overrides[get_db] = fake_db
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_chat_ask_stream_sends_sse_deltas(app) -> None:
    async def fake_events(*_args, **_kwargs):
        yield {
            "type": "meta",
            "citations": [
                {
                    "thread_id": str(THREAD_A),
                    "mailbox": SALES,
                    "subject": "Invoice dispute",
                    "state": "REQUIRES_HUMAN",
                    "urgency": "HIGH",
                    "snippet": "Please review",
                    "url_path": f"/threads/{THREAD_A}",
                }
            ],
            "retrieval_count": 1,
            "mailbox": SALES,
            "refused_write": False,
        }
        yield {"type": "delta", "text": CHUNK_ONE}
        yield {"type": "delta", "text": CHUNK_TWO}
        yield {"type": "done"}

    with patch("app.api.web.chat.chat_service.iter_ask_events", fake_events):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat/ask/stream",
                json={"message": "billing disputes waiting on review"},
            )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers.get("x-accel-buffering") == "no"
    parsed = [
        json.loads(line[5:].strip()) for line in resp.text.split("\n") if line.startswith("data:")
    ]
    assert parsed[0]["type"] == "meta"
    assert parsed[0]["citations"][0]["thread_id"] == str(THREAD_A)
    assert parsed[1] == {"type": "delta", "text": CHUNK_ONE}
    assert parsed[2] == {"type": "delta", "text": CHUNK_TWO}
    assert parsed[-1] == {"type": "done"}
