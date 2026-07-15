"""Unit tests for persist-only simulate ingest endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.simulate.ingest import router as simulate_router
from app.core.config import Settings
from app.core.dependencies import get_db, get_redis, get_settings
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.graph import IngestResultSchema


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(simulate_router)
    return app


@pytest.fixture
async def simulate_client() -> AsyncIterator[tuple[FastAPI, AsyncClient]]:
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield app, client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_simulate_ingest_happy_path_persists(
    simulate_client: tuple[FastAPI, AsyncClient],
) -> None:
    app, client = simulate_client

    settings = Settings(environment="local")
    session = AsyncMock()

    class _Begin:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: object) -> None:
            return None

    session.begin = MagicMock(return_value=_Begin())

    async def _db() -> AsyncMock:
        return session

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_redis] = lambda: redis

    email = EmailMessageSchema(
        message_id="sim-1",
        conversation_id="c1",
        mailbox="user@example.com",
        sender="vendor@example.com",
        subject="Hello",
        body_text="Body",
        received_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )
    context = ThreadContextSchema(
        conversation_id="c1",
        mailbox="user@example.com",
        subject="Hello",
        messages=[email],
    )

    from app.services import ingestion_service

    original_ingest = ingestion_service.ingest_simulated_message
    original_complete = ingestion_service.complete_ingest_dedup
    complete_mock = AsyncMock()
    ingestion_service.ingest_simulated_message = AsyncMock(  # type: ignore[method-assign]
        return_value=IngestResultSchema(
            message_id="sim-1",
            status="ingested",
            thread_id="t1",
            conversation_id="c1",
            thread_context=context,
        )
    )
    ingestion_service.complete_ingest_dedup = complete_mock  # type: ignore[method-assign]

    try:
        response = await client.post(
            "/simulate/ingest",
            json={
                "mailbox": "user@example.com",
                "message_id": "sim-1",
                "conversation_id": "c1",
                "sender": "vendor@example.com",
                "subject": "Hello",
                "body_text": "Body",
                "received_at": "2026-07-09T12:00:00Z",
            },
        )
    finally:
        ingestion_service.ingest_simulated_message = original_ingest  # type: ignore[method-assign]
        ingestion_service.complete_ingest_dedup = original_complete  # type: ignore[method-assign]

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ingested"
    assert body["message_id"] == "sim-1"
    complete_mock.assert_awaited_once_with(redis, "user@example.com", "sim-1")


@pytest.mark.asyncio
async def test_simulate_ingest_production_returns_404(
    simulate_client: tuple[FastAPI, AsyncClient],
) -> None:
    app, client = simulate_client

    settings = Settings(environment="production")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_redis] = lambda: AsyncMock()

    response = await client.post(
        "/simulate/ingest",
        json={
            "mailbox": "user@example.com",
            "message_id": "sim-1",
            "conversation_id": "c1",
            "sender": "vendor@example.com",
            "subject": "Hello",
            "body_text": "Body",
            "received_at": datetime(2026, 7, 9, 12, 0, tzinfo=UTC).isoformat(),
        },
    )

    assert response.status_code == 404
