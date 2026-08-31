"""Reply memory API — list approved tone refs and exclude from RAG."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import get_db
from app.repositories.reply_embedding_repo import ReplyMemoryListItem


@pytest.fixture
def api_user_role() -> str:
    return "admin"


def _reply(**overrides: object) -> ReplyMemoryListItem:
    base = {
        "id": uuid.uuid4(),
        "draft_id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "mailbox": "elise@example.com",
        "reply_text": "Thanks — sending the packet today.",
        "preview_line": "Thanks — sending the packet today.",
        "draft_subject": "Re: Packet",
        "sender_email": "client@example.com",
        "receiver_email": "elise@example.com",
        "reason_code": "similar",
        "reason_text": "Keep this tone",
        "original_email_preview": "Need drug screen results",
        "learning_note": "Keep this tone",
        "is_excluded": False,
        "created_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ReplyMemoryListItem.model_validate(base)


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
    assert body[0]["sender_email"] == "client@example.com"
    assert body[0]["draft_subject"] == "Re: Packet"
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
    session.commit = AsyncMock()

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
