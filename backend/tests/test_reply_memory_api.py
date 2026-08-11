"""Reply memory API — list approved tone refs and exclude from RAG."""

from __future__ import annotations

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
from app.repositories.reply_embedding_repo import ReplyEmbeddingSchema


@pytest.fixture
def local_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes="sales@example.com,clientrelations@example.com",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def _reply(**overrides: object) -> ReplyEmbeddingSchema:
    base = {
        "id": uuid.uuid4(),
        "draft_id": uuid.uuid4(),
        "mailbox": "elise@example.com",
        "reply_text": "Thanks — sending the packet today.",
        "original_email_preview": "Need drug screen results",
        "is_excluded": False,
        "created_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ReplyEmbeddingSchema.model_validate(base)


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
                role="admin",
                created_at=datetime.now(UTC),
            )

        application.dependency_overrides[get_current_user] = fake_user

        mock_session = MagicMock()
        mock_session.commit = AsyncMock()

        async def fake_db():
            yield mock_session

        application.dependency_overrides[get_db] = fake_db
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_list_reply_memory(app) -> None:
    rows = [_reply(), _reply(is_excluded=True, reply_text="Old style")]
    with patch(
        "app.api.web.reply_memory.reply_memory_service.list_memories",
        AsyncMock(return_value=rows),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/reply-memory")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["reply_text"] == rows[0].reply_text
    assert body[1]["is_excluded"] is True


@pytest.mark.asyncio
async def test_exclude_reply_memory(app) -> None:
    reply_id = uuid.uuid4()
    updated = _reply(id=reply_id, is_excluded=True)
    session = AsyncMock()
    session.commit = AsyncMock()

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db

    with patch(
        "app.api.web.reply_memory.reply_memory_service.set_excluded",
        AsyncMock(return_value=updated),
    ) as set_mock:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/api/reply-memory/{reply_id}",
                json={"is_excluded": True},
            )
    assert response.status_code == 200
    assert response.json()["is_excluded"] is True
    set_mock.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_exclude_reply_memory_not_found(app) -> None:
    session = AsyncMock()

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db

    with patch(
        "app.api.web.reply_memory.reply_memory_service.set_excluded",
        AsyncMock(return_value=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/api/reply-memory/{uuid.uuid4()}",
                json={"is_excluded": True},
            )
    assert response.status_code == 404
