"""API tests for POST /api/drafts/{id}/urgency."""

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
from app.models.schemas.draft import DraftResponseSchema
from app.models.schemas.urgency_feedback import UrgencyEditResponseSchema
from app.services.urgency_feedback_service import UrgencyEditResult


@pytest.fixture
def local_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes="elise@example.com",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def _draft(**overrides: object) -> DraftResponseSchema:
    base = {
        "id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "subject_line": "Re: Test",
        "reply_body": "Hello",
        "teaching_note": "Note",
        "urgency": "LOW",
        "urgency_reason": "Routine",
        "suggested_recipients": [],
        "forward_to": None,
        "suggested_actions": [],
        "routing_category": "billing",
    }
    base.update(overrides)
    return DraftResponseSchema.model_validate(base)


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
        application.dependency_overrides[get_db] = lambda: AsyncMock(
            commit=AsyncMock(),
            in_transaction=lambda: False,
        )
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_edit_urgency_ok(app) -> None:
    draft = _draft()
    result = UrgencyEditResult(
        response=UrgencyEditResponseSchema(
            urgency="HIGH",
            urgency_reason="SLA deadline",
            updated_at=datetime.now(UTC),
            draft_id=draft.id,
            thread_id=draft.thread_id,
        ),
        previous_urgency="LOW",
        mailbox="elise@example.com",
        routing_category="billing",
    )
    with (
        patch(
            "app.api.web.drafts.urgency_feedback_service.apply_manual_urgency_edit",
            AsyncMock(return_value=result),
        ),
        patch(
            "app.api.web.drafts.urgency_feedback_service.store_urgency_feedback_memory",
            AsyncMock(),
        ) as store_mock,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/drafts/{draft.id}/urgency",
                json={"new_urgency": "HIGH", "reason": "SLA deadline"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["urgency"] == "HIGH"
    assert body["urgency_reason"] == "SLA deadline"
    store_mock.assert_awaited_once()
    assert store_mock.await_args.kwargs["mailbox"] == "elise@example.com"
    assert store_mock.await_args.kwargs["previous_urgency"] == "LOW"


@pytest.mark.asyncio
async def test_edit_urgency_invalid_level_422(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/drafts/{uuid.uuid4()}/urgency",
            json={"new_urgency": "URGENT", "reason": "Bad level"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_edit_urgency_not_found_404(app) -> None:
    from app.core.exceptions import DraftNotFoundError

    with patch(
        "app.api.web.drafts.urgency_feedback_service.apply_manual_urgency_edit",
        AsyncMock(side_effect=DraftNotFoundError("Draft not found")),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/drafts/{uuid.uuid4()}/urgency",
                json={"new_urgency": "HIGH", "reason": "Because"},
            )
    assert resp.status_code == 404
