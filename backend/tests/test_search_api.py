"""Search API: auth, blank query, mailbox filter, response contract."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_user
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.models.schemas.search import SearchHit, SearchResponse

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


def _response() -> SearchResponse:
    return SearchResponse(
        query="drug screen packet",
        mailbox=SALES,
        hits=[
            SearchHit(
                thread_id=THREAD_ID,
                mailbox=SALES,
                conversation_id="conv-sales-packet",
                subject="Drug screen packet",
                state="DRAFTED",
                urgency="HIGH",
                snippet="Drug screen packet — Please send the packet by Friday.",
                score=0.0203125,
                last_message_at=datetime(2026, 8, 5, 15, 0, tzinfo=UTC),
            )
        ],
    )


@pytest.mark.asyncio
async def test_search_requires_auth(local_settings: Settings) -> None:
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
            resp = await client.get("/api/search", params={"q": "packet"})
        application.dependency_overrides.clear()
    assert resp.status_code == 401
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_search_happy_path_returns_ranked_hits(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch(
        "app.api.web.search.search_service.search_threads",
        AsyncMock(return_value=_response()),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/search",
                params={"q": "drug screen packet", "mailbox": "sales", "limit": 10},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "drug screen packet"
    assert body["mailbox"] == SALES
    assert len(body["hits"]) == 1
    hit = body["hits"][0]
    assert hit["thread_id"] == str(THREAD_ID)
    assert hit["mailbox"] == SALES
    assert hit["conversation_id"] == "conv-sales-packet"
    assert hit["subject"] == "Drug screen packet"
    assert hit["state"] == "DRAFTED"
    assert hit["urgency"] == "HIGH"
    assert hit["snippet"] == "Drug screen packet — Please send the packet by Friday."
    assert hit["score"] == pytest.approx(0.0203125)
    assert hit["last_message_at"] is not None


@pytest.mark.asyncio
async def test_search_blank_query_422(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/api/search")
        empty = await client.get("/api/search", params={"q": ""})
    assert missing.status_code == 422
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_search_whitespace_query_422(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/search", params={"q": "   "})
    assert resp.status_code == 422
    assert resp.json()["error_type"] == "EmptySearchQueryError"


@pytest.mark.asyncio
async def test_search_unknown_mailbox_404(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/search",
            params={"q": "packet", "mailbox": "unknown@example.com"},
        )
    assert resp.status_code == 404
    assert resp.json()["error_type"] == "UnknownMailboxError"


@pytest.mark.asyncio
async def test_search_limit_above_cap_422(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/search", params={"q": "packet", "limit": 26})
    assert resp.status_code == 422
