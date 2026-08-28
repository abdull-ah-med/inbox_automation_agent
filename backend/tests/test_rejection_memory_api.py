"""Rejection memory API — list rejects used as negative constraints; exclude from RAG."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import get_db
from app.repositories.rejection_memory_repo import RejectionMemoryListItem


@pytest.fixture
def api_user_role() -> str:
    return "admin"


def _row(**overrides: object) -> RejectionMemoryListItem:
    base = {
        "id": uuid.uuid4(),
        "draft_id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "mailbox": "elise@example.com",
        "routing_category": "billing",
        "reason_code": "wrong_tone",
        "note": "Do not promise same-day turnaround.",
        "reason_text": "Do not promise same-day turnaround.",
        "is_excluded": False,
        "created_at": datetime.now(UTC),
        "draft_subject": "Re: Invoice",
        "draft_body": "Hi Beau,\n\nWe can do that today.",
        "draft_body_preview": "Hi Beau,\n\nWe can do that today.",
        "preview_line": "Hi Beau, We can do that today.",
        "sender_email": "beau@example.com",
        "receiver_email": "elise@example.com",
    }
    base.update(overrides)
    return RejectionMemoryListItem.model_validate(base)


@pytest.mark.asyncio
async def test_list_rejection_memory(app) -> None:
    rows = [
        _row(),
        _row(is_excluded=True, note="Old constraint", draft_body_preview="Hi,"),
    ]
    with patch(
        "app.api.web.rejection_memory.rejection_memory_service.list_memories",
        AsyncMock(return_value=rows),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/rejection-memory")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["note"] == "Do not promise same-day turnaround."
    assert body[0]["sender_email"] == "beau@example.com"
    assert body[0]["draft_subject"] == "Re: Invoice"
    assert body[1]["is_excluded"] is True


@pytest.mark.asyncio
async def test_exclude_rejection_memory(app) -> None:
    memory_id = uuid.uuid4()
    updated = _row(id=memory_id, is_excluded=True)
    session = AsyncMock()
    session.commit = AsyncMock()

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db

    with patch(
        "app.api.web.rejection_memory.rejection_memory_service.set_excluded",
        AsyncMock(return_value=updated),
    ) as set_mock:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/api/rejection-memory/{memory_id}",
                json={"is_excluded": True},
            )
    assert response.status_code == 200
    assert response.json()["is_excluded"] is True
    set_mock.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_exclude_rejection_memory_not_found(app) -> None:
    session = AsyncMock()
    session.commit = AsyncMock()

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db

    with patch(
        "app.api.web.rejection_memory.rejection_memory_service.set_excluded",
        AsyncMock(return_value=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/api/rejection-memory/{uuid.uuid4()}",
                json={"is_excluded": True},
            )
    assert response.status_code == 404
