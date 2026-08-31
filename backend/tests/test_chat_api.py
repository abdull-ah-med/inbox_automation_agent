"""Chat ask API: auth, grounded citations, blank message, write-intent."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_user
from app.core.exceptions import ChatError
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.models.schemas.chat import ChatAskResponse, ChatCitation

SALES = "sales@example.com"
THREAD_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture
def local_settings() -> Settings:
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


@pytest.fixture
def app(local_settings: Settings):
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
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


def _ask_response() -> ChatAskResponse:
    return ChatAskResponse(
        answer="The overdue billing dispute is waiting on review.",
        citations=[
            ChatCitation(
                thread_id=THREAD_ID,
                mailbox=SALES,
                subject="Invoice dispute — overdue billing",
                state="REQUIRES_HUMAN",
                urgency="HIGH",
                snippet="Invoice dispute — Please review the overdue billing packet.",
                url_path=f"/threads/{THREAD_ID}",
            )
        ],
        retrieval_count=1,
        mailbox=SALES,
        refused_write=False,
    )


@pytest.mark.asyncio
async def test_chat_ask_requires_auth(local_settings: Settings) -> None:
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
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat/ask",
                json={"message": "billing disputes waiting on review"},
            )
        application.dependency_overrides.clear()
    assert resp.status_code == 401
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_chat_ask_returns_grounded_answer_and_citations(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch(
        "app.api.web.chat.chat_service.ask",
        AsyncMock(return_value=_ask_response()),
    ) as ask_mock:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat/ask",
                json={
                    "message": "billing disputes waiting on review",
                    "mailbox": SALES,
                    "limit": 10,
                },
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "The overdue billing dispute is waiting on review."
    assert body["retrieval_count"] == 1
    assert body["mailbox"] == SALES
    assert body["refused_write"] is False
    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    assert citation["thread_id"] == str(THREAD_ID)
    assert citation["url_path"] == f"/threads/{THREAD_ID}"
    assert citation["mailbox"] == SALES
    assert citation["subject"] == "Invoice dispute — overdue billing"
    assert citation["state"] == "REQUIRES_HUMAN"
    assert citation["urgency"] == "HIGH"
    kwargs = ask_mock.await_args.kwargs
    assert kwargs["message"] == "billing disputes waiting on review"
    assert kwargs["mailbox"] == SALES
    assert kwargs["limit"] == 10
    assert kwargs["history"] == []


@pytest.mark.asyncio
async def test_chat_ask_forwards_history_turns(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch(
        "app.api.web.chat.chat_service.ask",
        AsyncMock(return_value=_ask_response()),
    ) as ask_mock:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat/ask",
                json={
                    "message": "tell me more",
                    "history": [
                        {
                            "role": "user",
                            "content": "billing disputes waiting on review",
                        },
                        {
                            "role": "assistant",
                            "content": "The overdue billing dispute is waiting on review.",
                        },
                    ],
                },
            )
    assert resp.status_code == 200
    history = ask_mock.await_args.kwargs["history"]
    assert [turn.content for turn in history] == [
        "billing disputes waiting on review",
        "The overdue billing dispute is waiting on review.",
    ]
    assert [turn.role for turn in history] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_chat_ask_forwards_cited_thread_ids_on_history(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch(
        "app.api.web.chat.chat_service.ask",
        AsyncMock(return_value=_ask_response()),
    ) as ask_mock:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat/ask",
                json={
                    "message": "tell me more",
                    "history": [
                        {
                            "role": "user",
                            "content": "billing disputes waiting on review",
                        },
                        {
                            "role": "assistant",
                            "content": "The overdue billing dispute is waiting on review.",
                            "citations": [
                                {
                                    "thread_id": str(THREAD_ID),
                                    "subject": "Invoice dispute — overdue billing",
                                }
                            ],
                        },
                    ],
                },
            )
    assert resp.status_code == 200
    history = ask_mock.await_args.kwargs["history"]
    assert history[1].citations[0].thread_id == THREAD_ID
    assert history[1].citations[0].subject == "Invoice dispute — overdue billing"


@pytest.mark.asyncio
async def test_chat_ask_follow_up_with_long_assistant_history_is_not_422(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    answer = "Latest on O'Mason Lumber is the users thread. " + ("x" * 520)
    with patch(
        "app.api.web.chat.chat_service.ask",
        AsyncMock(return_value=_ask_response()),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat/ask",
                json={
                    "message": "what s happening here?",
                    "history": [
                        {
                            "role": "user",
                            "content": "give me the latest on omason",
                        },
                        {"role": "assistant", "content": answer},
                    ],
                },
            )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_chat_ask_blank_message_422(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post("/api/chat/ask", json={})
        empty = await client.post("/api/chat/ask", json={"message": ""})
        whitespace = await client.post("/api/chat/ask", json={"message": "   "})
    assert missing.status_code == 422
    assert empty.status_code == 422
    assert whitespace.status_code == 422


@pytest.mark.asyncio
async def test_chat_ask_write_intent_returns_refused_write(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    refused = ChatAskResponse(
        answer="I cannot approve or send mail. Open the thread in review to act.",
        citations=[
            ChatCitation(
                thread_id=THREAD_ID,
                mailbox=SALES,
                subject="Invoice dispute — overdue billing",
                state="REQUIRES_HUMAN",
                urgency="HIGH",
                snippet=None,
                url_path=f"/threads/{THREAD_ID}",
            )
        ],
        retrieval_count=1,
        mailbox=SALES,
        refused_write=True,
    )
    with patch(
        "app.api.web.chat.chat_service.ask",
        AsyncMock(return_value=refused),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat/ask",
                json={"message": "approve this and send"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["refused_write"] is True
    assert body["citations"][0]["url_path"] == f"/threads/{THREAD_ID}"


@pytest.mark.asyncio
async def test_chat_ask_llm_failure_maps_to_502(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch(
        "app.api.web.chat.chat_service.ask",
        AsyncMock(side_effect=ChatError("Claude chat failed")),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat/ask",
                json={"message": "billing disputes"},
            )
    assert resp.status_code == 502
    assert resp.json()["error_type"] == "ChatError"
