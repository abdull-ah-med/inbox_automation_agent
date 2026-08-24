"""HTTP contracts for related-thread list, apply-treatment, and review."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_user
from app.core.exceptions import ThreadNotFoundError, ThreadStateError
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.models.schemas.related import RelatedThreadItem, RelatedThreadList

SOURCE_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
SIBLING_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2")


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
        application.dependency_overrides[get_db] = lambda: AsyncMock(
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


def _item() -> RelatedThreadItem:
    return RelatedThreadItem(
        thread_id=SIBLING_ID,
        mailbox="sales@example.com",
        subject="SampleClient follow-up 8/14",
        sender="rep@sample-client.example.com",
        last_message_at=datetime(2026, 8, 14, 15, 0, tzinfo=UTC),
        urgency="NORMAL",
        score=0.02,
        status="proposed",
    )


@pytest.mark.asyncio
async def test_get_related_siblings_returns_items(app) -> None:
    with patch(
        "app.api.web.threads.related_thread_service.list_related",
        AsyncMock(return_value=RelatedThreadList(items=[_item()])),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                f"/api/threads/{SOURCE_ID}/related",
                params={"purpose": "siblings"},
            )

    assert response.status_code == 200
    body = response.json()
    assert [row["thread_id"] for row in body["items"]] == [str(SIBLING_ID)]
    assert body["items"][0]["subject"] == "SampleClient follow-up 8/14"
    assert body["items"][0]["status"] == "proposed"


@pytest.mark.asyncio
async def test_get_related_unknown_thread_404(app) -> None:
    with patch(
        "app.api.web.threads.related_thread_service.list_related",
        AsyncMock(side_effect=ThreadNotFoundError("Thread not found")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                f"/api/threads/{SOURCE_ID}/related",
                params={"purpose": "siblings"},
            )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_apply_treatment_returns_applied_ids(app) -> None:
    with patch(
        "app.api.web.threads.related_thread_service.apply_treatment",
        AsyncMock(return_value=[SIBLING_ID]),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/threads/{SOURCE_ID}/apply-treatment",
                json={
                    "treatment": "no_reply",
                    "thread_ids": [str(SIBLING_ID)],
                    "reason": "Same SampleClient drip",
                },
            )

    assert response.status_code == 200
    assert response.json()["applied_thread_ids"] == [str(SIBLING_ID)]


@pytest.mark.asyncio
async def test_review_related_returns_status(app) -> None:
    with patch(
        "app.api.web.threads.related_thread_service.review_related",
        AsyncMock(return_value={"status": "dismissed"}),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/threads/{SOURCE_ID}/related/{SIBLING_ID}/review",
                json={"status": "dismissed"},
            )

    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"


@pytest.mark.asyncio
async def test_urgency_feedback_returns_reverted_urgency(app) -> None:
    with patch(
        "app.repositories.thread_repo.get_by_id",
        AsyncMock(
            return_value=type(
                "T",
                (),
                {
                    "id": SOURCE_ID,
                    "mailbox": "sales@example.com",
                    "state": "DRAFTED",
                },
            )()
        ),
    ), patch(
        "app.api.web.threads.recurrence_service.apply_urgency_feedback",
        AsyncMock(return_value="NORMAL"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/threads/{SOURCE_ID}/urgency-feedback",
                json={"action": "wrong_escalation"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "wrong_escalation"
    assert body["urgency"] == "NORMAL"
    assert body["state"] == "DRAFTED"


@pytest.mark.asyncio
async def test_urgency_feedback_without_escalation_is_409(app) -> None:
    with patch(
        "app.repositories.thread_repo.get_by_id",
        AsyncMock(
            return_value=type(
                "T",
                (),
                {
                    "id": SOURCE_ID,
                    "mailbox": "sales@example.com",
                    "state": "DRAFTED",
                },
            )()
        ),
    ), patch(
        "app.api.web.threads.recurrence_service.apply_urgency_feedback",
        AsyncMock(side_effect=ThreadStateError("No automatic urgency bump to reverse")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/threads/{SOURCE_ID}/urgency-feedback",
                json={"action": "wrong_escalation"},
            )

    assert response.status_code == 409
    assert "bump" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_review_related_conflict_is_409(app) -> None:
    with patch(
        "app.api.web.threads.related_thread_service.review_related",
        AsyncMock(side_effect=ThreadStateError("Association was not proposed")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/threads/{SOURCE_ID}/related/{SIBLING_ID}/review",
                json={"status": "confirmed"},
            )

    assert response.status_code == 409
