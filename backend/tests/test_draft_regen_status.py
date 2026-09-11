"""Draft rewrite status is persisted on the thread (survives reload).

Worked example: Elise confirms rewrite → thread.draft_regen_status becomes
"running" before Sonnet finishes. Reload still shows regenerating; when the
new draft lands, status returns to "idle".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    get_anthropic_client,
    get_db,
    get_graph_client,
    get_openai_client,
    get_redis,
)
from app.core.dependencies_auth import get_current_user
from app.main import create_app
from app.models.db.thread import Thread
from app.models.schemas.auth import UserMe
from app.models.schemas.email import ThreadStateEnum
from app.repositories import thread_repo
from app.services import draft_regen_job_service

pytestmark = pytest.mark.db

SALES = "sales@example.com"
T_OPEN = datetime(2026, 9, 11, 15, 0, tzinfo=UTC)


async def _seed_thread(session, *, conversation_id: str) -> Thread:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=SALES,
        conversation_id=conversation_id,
        subject="SampleLabVendor invoice",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=T_OPEN,
    )
    session.add(thread)
    await session.commit()
    return thread


@pytest.mark.asyncio
async def test_begin_regen_persists_running_on_thread(db_session) -> None:
    thread = await _seed_thread(db_session, conversation_id="t-regen-running")

    started = await draft_regen_job_service.begin_regen(
        db_session,
        thread.id,
        instruction="Process the SampleLab invoice and tell Beau.",
    )
    await db_session.commit()

    assert started is True
    refreshed = await thread_repo.get_by_id_trusted(db_session, thread.id)
    assert refreshed is not None
    assert refreshed.draft_regen_status == "running"
    assert refreshed.draft_regen_error is None
    assert refreshed.draft_regen_started_at is not None


@pytest.mark.asyncio
async def test_finish_regen_clears_running_status(db_session) -> None:
    thread = await _seed_thread(db_session, conversation_id="t-regen-finish")
    await draft_regen_job_service.begin_regen(
        db_session,
        thread.id,
        instruction="Rewrite with the correct process.",
    )
    await db_session.commit()

    await draft_regen_job_service.finish_regen(db_session, thread.id)
    await db_session.commit()

    refreshed = await thread_repo.get_by_id_trusted(db_session, thread.id)
    assert refreshed is not None
    assert refreshed.draft_regen_status == "idle"
    assert refreshed.draft_regen_started_at is None


@pytest.mark.asyncio
async def test_fail_regen_persists_failed_status_with_error(db_session) -> None:
    thread = await _seed_thread(db_session, conversation_id="t-regen-fail")
    await draft_regen_job_service.begin_regen(
        db_session,
        thread.id,
        instruction="Rewrite with the correct process.",
    )
    await db_session.commit()

    await draft_regen_job_service.fail_regen(
        db_session,
        thread.id,
        error="Sonnet timed out",
    )
    await db_session.commit()

    refreshed = await thread_repo.get_by_id_trusted(db_session, thread.id)
    assert refreshed is not None
    assert refreshed.draft_regen_status == "failed"
    assert refreshed.draft_regen_error == "Sonnet timed out"


@pytest.fixture
def local_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes=SALES,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


@pytest.mark.asyncio
async def test_regenerate_endpoint_returns_202_and_marks_running(
    local_settings: Settings,
    db_session,
) -> None:
    thread = await _seed_thread(db_session, conversation_id="t-regen-api-202")
    get_settings.cache_clear()
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: local_settings

    async def fake_user() -> UserMe:
        return UserMe(
            id=uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            email="elise@sample-site.example.com",
            role="user",
            created_at=datetime.now(UTC),
        )

    application.dependency_overrides[get_current_user] = fake_user
    application.dependency_overrides[get_db] = lambda: db_session
    application.dependency_overrides[get_redis] = lambda: None
    application.dependency_overrides[get_anthropic_client] = lambda: None
    application.dependency_overrides[get_openai_client] = lambda: None
    application.dependency_overrides[get_graph_client] = lambda: None

    with patch(
        "app.api.web.threads.draft_regen_job_service.begin_regen",
        AsyncMock(return_value=True),
    ) as begin_mock:
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                f"/api/threads/{thread.id}/regenerate-draft",
                json={"instruction": "Process the SampleLab invoice and tell Beau."},
            )

    application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "running"
    assert body["thread_id"] == str(thread.id)
    assert body["draft_regen_in_progress"] is True
    assert begin_mock.await_count == 1
