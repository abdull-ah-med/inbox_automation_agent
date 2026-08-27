"""GET /api/threads/{thread_id}/messages/{message_id}/html — Outlook View body.

Oracles are literal Graph HTML fragments and cid→data rewrites. Ingest Prefer-text
storage is unrelated: stored body_text must not appear as the HTML payload.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    get_anthropic_client,
    get_db,
    get_graph_client,
    get_openai_client,
    get_redis,
)
from app.core.dependencies_auth import get_current_user
from app.graph.client import GraphClient
from app.main import create_app
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.auth import UserMe
from app.models.schemas.email import ThreadStateEnum
from app.models.schemas.graph import (
    GraphFileAttachmentSchema,
    GraphMessageBodySchema,
    GraphMessageSchema,
)
from app.services.message_html_service import replace_cid_images

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
OTHER_MAILBOX = "other@example.com"
STORED_PLAIN = "Please review the overdue billing packet."
GRAPH_HTML = '<html><body><b>Invoice</b> <img src="cid:logo@123"></body></html>'
PNG_B64 = "iVBORw0KGgo="
T0 = datetime(2026, 8, 22, 14, 0, tzinfo=UTC)


@pytest.fixture
def local_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def test_replace_cid_images_swaps_content_id_for_data_uri() -> None:
    html = '<img src="cid:logo@123" alt="x">'
    attachments = [
        GraphFileAttachmentSchema(
            id="att-1",
            content_type="image/png",
            content_bytes=PNG_B64,
            content_id="logo@123",
            is_inline=True,
        )
    ]
    out = replace_cid_images(html, attachments)
    assert "cid:logo@123" not in out
    assert f"data:image/png;base64,{PNG_B64}" in out


async def _seed_thread_message(
    session: AsyncSession,
    *,
    mailbox: str = MAILBOX,
    thread_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    tid = thread_id or uuid.uuid4()
    mid = message_id or uuid.uuid4()
    session.add(
        Thread(
            id=tid,
            mailbox=mailbox,
            conversation_id=f"conv-{tid}",
            subject="Invoice dispute",
            state=ThreadStateEnum.DRAFTED.value,
            last_message_at=T0,
        )
    )
    await session.flush()
    session.add(
        Message(
            id=mid,
            thread_id=tid,
            graph_message_id=f"graph-{mid}",
            direction="inbound",
            sender="alice@example.com",
            body_text=STORED_PLAIN,
            body_preview="Please review",
            unique_body_text=STORED_PLAIN,
            body_content_type="text",
            received_at=T0,
            to_recipients=[mailbox],
            has_attachments=False,
        )
    )
    await session.commit()
    return tid, mid


def _auth_overrides(
    application,
    settings: Settings,
    session: AsyncSession,
    graph: GraphClient,
) -> None:
    get_settings.cache_clear()
    application.dependency_overrides[get_settings] = lambda: settings

    async def fake_user() -> UserMe:
        return UserMe(
            id=uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            email="reviewer@example.com",
            role="user",
            created_at=datetime.now(UTC),
        )

    application.dependency_overrides[get_current_user] = fake_user
    application.dependency_overrides[get_db] = lambda: session
    application.dependency_overrides[get_redis] = lambda: None
    application.dependency_overrides[get_anthropic_client] = lambda: None
    application.dependency_overrides[get_openai_client] = lambda: None
    application.dependency_overrides[get_graph_client] = lambda: graph


@pytest.mark.asyncio
async def test_message_html_returns_graph_html_not_stored_plain(
    local_settings: Settings,
    db_session: AsyncSession,
) -> None:
    thread_id, message_id = await _seed_thread_message(db_session)
    graph = AsyncMock(spec=GraphClient)
    graph.get_message_html = AsyncMock(
        return_value=GraphMessageSchema(
            id=f"graph-{message_id}",
            body=GraphMessageBodySchema(content_type="html", content=GRAPH_HTML),
        )
    )
    graph.list_message_attachments = AsyncMock(
        return_value=[
            GraphFileAttachmentSchema(
                id="att-1",
                content_type="image/png",
                content_bytes=PNG_B64,
                content_id="logo@123",
                is_inline=True,
            )
        ]
    )

    application = create_app()
    _auth_overrides(application, local_settings, db_session, graph)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            resp = await client.get(f"/api/threads/{thread_id}/messages/{message_id}/html")
    finally:
        application.dependency_overrides.clear()
        get_settings.cache_clear()

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["content_type"] == "html"
    assert "<b>Invoice</b>" in payload["html"]
    assert STORED_PLAIN not in payload["html"]
    assert "cid:logo@123" not in payload["html"]
    assert f"data:image/png;base64,{PNG_B64}" in payload["html"]
    graph.get_message_html.assert_awaited_once_with(MAILBOX, f"graph-{message_id}")


@pytest.mark.asyncio
async def test_message_html_404_when_message_not_in_thread(
    local_settings: Settings,
    db_session: AsyncSession,
) -> None:
    thread_id, _message_id = await _seed_thread_message(db_session)
    other_thread_id, other_message_id = await _seed_thread_message(db_session)
    graph = AsyncMock(spec=GraphClient)

    application = create_app()
    _auth_overrides(application, local_settings, db_session, graph)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            resp = await client.get(f"/api/threads/{thread_id}/messages/{other_message_id}/html")
    finally:
        application.dependency_overrides.clear()
        get_settings.cache_clear()

    assert resp.status_code == 404
    graph.get_message_html.assert_not_awaited()
    _ = other_thread_id


@pytest.mark.asyncio
async def test_message_html_404_when_mailbox_not_allowed(
    local_settings: Settings,
    db_session: AsyncSession,
) -> None:
    thread_id, message_id = await _seed_thread_message(
        db_session,
        mailbox=OTHER_MAILBOX,
    )
    graph = AsyncMock(spec=GraphClient)

    application = create_app()
    _auth_overrides(application, local_settings, db_session, graph)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            resp = await client.get(f"/api/threads/{thread_id}/messages/{message_id}/html")
    finally:
        application.dependency_overrides.clear()
        get_settings.cache_clear()

    assert resp.status_code == 404
    graph.get_message_html.assert_not_awaited()
