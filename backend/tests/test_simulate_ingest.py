"""Unit tests for persist + triage simulate ingest endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.simulate.ingest import router as simulate_router
from app.core.config import Settings
from app.core.dependencies import get_anthropic_client, get_db, get_redis, get_settings
from app.llm.prompts import PROMPT_VERSION
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import EmailTriageState
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
async def test_simulate_ingest_happy_path_includes_triage(
    simulate_client: tuple[FastAPI, AsyncClient],
) -> None:
    app, client = simulate_client

    settings = Settings(
        environment="local",
        enable_dev_routes=True,
        dev_api_key="test-dev-key",
        anthropic_api_key="test-key",
        target_mailboxes="user@example.com",
    )
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
    anthropic = AsyncMock()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_anthropic_client] = lambda: anthropic

    email = EmailMessageSchema(
        message_id="sim-1",
        conversation_id="c1",
        mailbox="user@example.com",
        sender="vendor@example.com",
        subject="Hello",
        body_text="Body",
        received_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["user@example.com"],
    )
    context = ThreadContextSchema(
        conversation_id="c1",
        mailbox="user@example.com",
        subject="Hello",
        messages=[email],
    )
    ingest_result = IngestResultSchema(
        message_id="sim-1",
        status="ingested",
        thread_id="t1",
        conversation_id="c1",
        thread_context=context,
    )
    triage = TriageResultSchema(
        is_spam=False,
        has_action_items=True,
        action_items_summary="Reply",
        needs_context=False,
    )

    async def _run_after_ingest(**_: object) -> EmailTriageState:
        return EmailTriageState(
            original_email=email,
            thread_context=context,
            triage=triage,
            draft=DraftSchema(
                subject_line="Re: Hello",
                reply_body="Thanks for reaching out.",
                teaching_note="Acknowledge and offer next steps.",
                urgency="NORMAL",
                urgency_reason="Routine inbound request",
            ),
            draft_status="DRAFTED",
        )

    from app.services import ingestion_service

    original_ingest = ingestion_service.ingest_simulated_message
    original_complete = ingestion_service.complete_ingest_dedup
    complete_mock = AsyncMock()
    ingestion_service.ingest_simulated_message = AsyncMock(  # type: ignore[method-assign]
        return_value=ingest_result
    )
    ingestion_service.complete_ingest_dedup = complete_mock  # type: ignore[method-assign]

    try:
        with patch(
            "app.api.simulate.ingest.pipeline_service.run_after_ingest",
            new=AsyncMock(side_effect=_run_after_ingest),
        ) as triage_mock:
            response = await client.post(
                "/simulate/ingest",
                headers={"X-Dev-Api-Key": "test-dev-key"},
                json={
                    "mailbox": "user@example.com",
                    "message_id": "sim-1",
                    "conversation_id": "c1",
                    "sender": "vendor@example.com",
                    "subject": "Hello",
                    "body_text": "Body",
                    "received_at": "2026-07-09T12:00:00Z",
                    "to_recipients": ["user@example.com"],
                },
            )
    finally:
        ingestion_service.ingest_simulated_message = original_ingest  # type: ignore[method-assign]
        ingestion_service.complete_ingest_dedup = original_complete  # type: ignore[method-assign]

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ingested"
    assert body["message_id"] == "sim-1"
    assert body["draft_status"] == "DRAFTED"
    assert body["prompt_version"] == PROMPT_VERSION
    assert body["triage"]["has_action_items"] is True
    assert body["draft"]["subject_line"] == "Re: Hello"
    assert body["draft"]["urgency"] == "NORMAL"
    assert "confidence" not in body["draft"]
    triage_mock.assert_awaited_once()
    complete_mock.assert_awaited_once_with(redis, "user@example.com", "sim-1")


@pytest.mark.asyncio
async def test_simulate_ingest_releases_dedup_when_triage_fails(
    simulate_client: tuple[FastAPI, AsyncClient],
) -> None:
    app, client = simulate_client

    settings = Settings(
        environment="local",
        enable_dev_routes=True,
        dev_api_key="test-dev-key",
        target_mailboxes="user@example.com",
    )
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
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_anthropic_client] = lambda: AsyncMock()

    from app.services import ingestion_service

    original_ingest = ingestion_service.ingest_simulated_message
    original_complete = ingestion_service.complete_ingest_dedup
    original_release = ingestion_service.release_ingest_dedup
    complete_mock = AsyncMock()
    release_mock = AsyncMock()
    ingestion_service.ingest_simulated_message = AsyncMock(  # type: ignore[method-assign]
        return_value=IngestResultSchema(
            message_id="sim-1",
            status="ingested",
            thread_id="t1",
            conversation_id="c1",
            thread_context=ThreadContextSchema(
                conversation_id="c1",
                mailbox="user@example.com",
                subject="Hello",
                messages=[],
            ),
        )
    )
    ingestion_service.complete_ingest_dedup = complete_mock  # type: ignore[method-assign]
    ingestion_service.release_ingest_dedup = release_mock  # type: ignore[method-assign]

    try:
        with patch(
            "app.api.simulate.ingest.pipeline_service.run_after_ingest",
            new=AsyncMock(side_effect=RuntimeError("haiku down")),
        ):
            response = await client.post(
                "/simulate/ingest",
                headers={"X-Dev-Api-Key": "test-dev-key"},
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
        ingestion_service.release_ingest_dedup = original_release  # type: ignore[method-assign]

    assert response.status_code == 500
    complete_mock.assert_not_awaited()
    release_mock.assert_awaited_once_with(redis, "user@example.com", "sim-1")


@pytest.mark.asyncio
async def test_simulate_ingest_rejects_mailbox_outside_allowlist(
    simulate_client: tuple[FastAPI, AsyncClient],
) -> None:
    app, client = simulate_client

    settings = Settings(
        environment="local",
        enable_dev_routes=True,
        dev_api_key="test-dev-key",
        target_mailboxes="elise@example.com",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_redis] = lambda: AsyncMock()
    app.dependency_overrides[get_anthropic_client] = lambda: AsyncMock()

    response = await client.post(
        "/simulate/ingest",
        headers={"X-Dev-Api-Key": "test-dev-key"},
        json={
            "mailbox": "attacker@example.com",
            "message_id": "sim-1",
            "conversation_id": "c1",
            "sender": "vendor@example.com",
            "subject": "Hello",
            "body_text": "Body",
            "received_at": "2026-07-09T12:00:00Z",
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_simulate_ingest_duplicate_skips_triage(
    simulate_client: tuple[FastAPI, AsyncClient],
) -> None:
    app, client = simulate_client

    settings = Settings(
        environment="local",
        enable_dev_routes=True,
        dev_api_key="test-dev-key",
        target_mailboxes="user@example.com",
    )
    session = AsyncMock()

    class _Begin:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: object) -> None:
            return None

    session.begin = MagicMock(return_value=_Begin())

    async def _db() -> AsyncMock:
        return session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_redis] = lambda: AsyncMock()
    app.dependency_overrides[get_anthropic_client] = lambda: AsyncMock()

    from app.services import ingestion_service

    original_ingest = ingestion_service.ingest_simulated_message
    ingestion_service.ingest_simulated_message = AsyncMock(  # type: ignore[method-assign]
        return_value=IngestResultSchema(message_id="sim-1", status="duplicate")
    )

    try:
        with patch(
            "app.api.simulate.ingest.pipeline_service.run_after_ingest",
            new=AsyncMock(),
        ) as triage:
            response = await client.post(
                "/simulate/ingest",
                headers={"X-Dev-Api-Key": "test-dev-key"},
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

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "duplicate"
    assert body["triage"] is None
    triage.assert_not_awaited()


@pytest.mark.asyncio
async def test_simulate_ingest_production_returns_404(
    simulate_client: tuple[FastAPI, AsyncClient],
) -> None:
    app, client = simulate_client

    settings = Settings(environment="production")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_redis] = lambda: AsyncMock()
    app.dependency_overrides[get_anthropic_client] = lambda: AsyncMock()

    response = await client.post(
        "/simulate/ingest",
        headers={"X-Dev-Api-Key": "test-dev-key"},
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


@pytest.mark.asyncio
async def test_simulate_ingest_staging_returns_404(
    simulate_client: tuple[FastAPI, AsyncClient],
) -> None:
    app, client = simulate_client

    settings = Settings(environment="staging")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_redis] = lambda: AsyncMock()
    app.dependency_overrides[get_anthropic_client] = lambda: AsyncMock()

    response = await client.post(
        "/simulate/ingest",
        headers={"X-Dev-Api-Key": "test-dev-key"},
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

    assert response.status_code == 404
