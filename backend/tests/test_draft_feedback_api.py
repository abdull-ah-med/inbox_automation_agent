"""Draft feedback API route tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_user
from app.core.exceptions import DraftNotFoundError
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.models.schemas.draft import DraftResponseSchema


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


def _draft_response(**overrides: object) -> DraftResponseSchema:
    base = {
        "id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "subject_line": "Re: Test",
        "reply_body": "Hello",
        "teaching_note": "Acknowledge",
        "urgency": "NORMAL",
        "urgency_reason": "Routine",
        "suggested_recipients": [],
        "forward_to": None,
        "suggested_actions": [],
        "approved_at": datetime.now(UTC),
        "rejected_at": None,
        "edited_body": None,
        "feedback_note": None,
        "feedback_action": "approve",
        "context_match_confidence": None,
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

        mock_session = MagicMock()
        mock_session.commit = AsyncMock()

        async def fake_db():
            yield mock_session

        application.dependency_overrides[get_db] = fake_db
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_approve_requires_auth(local_settings: Settings) -> None:
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
            resp = await client.post(f"/api/drafts/{uuid.uuid4()}/approve", json={})
        application.dependency_overrides.clear()
    assert resp.status_code == 401
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_approve_ok(app) -> None:
    draft = _draft_response()
    with (
        patch(
            "app.api.web.drafts.draft_feedback_service.approve_draft",
            AsyncMock(return_value=draft),
        ),
        patch(
            "app.api.web.drafts.draft_feedback_service.store_approved_reply_memory",
            AsyncMock(),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/drafts/{draft.id}/approve", json={})
    assert resp.status_code == 200
    assert resp.json()["feedback_action"] == "approve"
    assert resp.json()["subject"] == "Re: Test"
    assert resp.json()["body"] == "Hello"
    assert "subject_line" not in resp.json()
    assert "reply_body" not in resp.json()


@pytest.mark.asyncio
async def test_reject_requires_note(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/drafts/{uuid.uuid4()}/reject", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reject_requires_reason_code(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/drafts/{uuid.uuid4()}/reject",
            json={"feedback_note": "Bad tone"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reject_ok(app) -> None:
    draft = _draft_response(
        approved_at=None,
        rejected_at=datetime.now(UTC),
        feedback_action="reject",
        feedback_note="Bad tone",
        feedback_reason_code="tone",
    )
    with patch(
        "app.api.web.drafts.draft_feedback_service.reject_draft",
        AsyncMock(return_value=draft),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/drafts/{draft.id}/reject",
                json={"feedback_note": "Bad tone", "reason_code": "tone"},
            )
    assert resp.status_code == 200
    assert resp.json()["feedback_action"] == "reject"


@pytest.mark.asyncio
async def test_reject_process_note_over_2000_is_422(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/drafts/{uuid.uuid4()}/reject",
            json={
                "feedback_note": "Wrong process",
                "reason_code": "incomplete",
                "process_note": "a" * 2001,
            },
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reject_with_process_note_passes_sentence_to_service(app) -> None:
    process = (
        "In this case I would process the SampleLab invoice and tell Beau "
        "the rebill is with Harmeyer."
    )
    draft = _draft_response(
        approved_at=None,
        rejected_at=datetime.now(UTC),
        feedback_action="reject",
        feedback_note="DraftAssistant drafted a letter. This is an SampleLab invoice.",
        feedback_reason_code="incomplete",
    )
    with (
        patch(
            "app.api.web.drafts.draft_feedback_service.reject_draft",
            AsyncMock(return_value=draft),
        ) as reject_mock,
        patch(
            "app.api.web.drafts.draft_feedback_service.store_rejection_memory",
            AsyncMock(),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/drafts/{draft.id}/reject",
                json={
                    "feedback_note": "DraftAssistant drafted a letter. This is an SampleLab invoice.",
                    "reason_code": "incomplete",
                    "process_note": process,
                },
            )
    assert resp.status_code == 200
    assert reject_mock.await_args.kwargs["process_note"] == process


@pytest.mark.asyncio
async def test_wrong_ok(app) -> None:
    draft = _draft_response(
        approved_at=None,
        feedback_action="wrong",
        feedback_note="Should escalate",
        feedback_reason_code="wrong_action",
    )
    with patch(
        "app.api.web.drafts.draft_feedback_service.mark_wrong",
        AsyncMock(return_value=draft),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/drafts/{draft.id}/wrong",
                json={"feedback_note": "Should escalate", "reason_code": "wrong_action"},
            )
    assert resp.status_code == 200
    assert resp.json()["feedback_action"] == "wrong"


@pytest.mark.asyncio
async def test_approve_not_found_404(app) -> None:
    with patch(
        "app.api.web.drafts.draft_feedback_service.approve_draft",
        AsyncMock(side_effect=DraftNotFoundError("missing")),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/drafts/{uuid.uuid4()}/approve", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_approve_note_without_scope_422(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/drafts/{uuid.uuid4()}/approve",
            json={"approval_note": "Soften tone"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_approve_with_learning_context_ok(app) -> None:
    draft = _draft_response(
        approved_at=datetime.now(UTC),
        feedback_action="approve",
        edited_body="Edited hello",
        approval_note="Soften tone",
        approval_scope="similar",
    )
    with (
        patch(
            "app.api.web.drafts.draft_feedback_service.approve_draft",
            AsyncMock(return_value=draft),
        ) as approve_mock,
        patch(
            "app.api.web.drafts.draft_feedback_service.store_approved_reply_memory",
            AsyncMock(),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/drafts/{draft.id}/approve",
                json={
                    "edited_body": "Edited hello",
                    "approval_note": "Soften tone",
                    "approval_scope": "similar",
                },
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_note"] == "Soften tone"
    assert body["approval_scope"] == "similar"
    assert body["edited_body"] == "Edited hello"
    approve_mock.assert_awaited_once()
    assert approve_mock.await_args.kwargs["approval_note"] == "Soften tone"
    assert approve_mock.await_args.kwargs["approval_scope"] == "similar"


@pytest.mark.asyncio
async def test_reject_schedules_rejection_memory_in_background(app) -> None:
    """Reject returns the draft without awaiting rejection-memory work."""
    draft = _draft_response(
        approved_at=None,
        rejected_at=datetime.now(UTC),
        feedback_action="reject",
        feedback_note="Bad tone",
        feedback_reason_code="tone",
    )
    with (
        patch(
            "app.api.web.drafts.draft_feedback_service.reject_draft",
            AsyncMock(return_value=draft),
        ),
        patch("app.api.web.drafts.enqueue") as enqueue_mock,
        patch(
            "app.api.web.drafts.draft_feedback_service.store_rejection_memory",
            new_callable=AsyncMock,
        ) as memory_mock,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/drafts/{draft.id}/reject",
                json={"feedback_note": "Bad tone", "reason_code": "tone"},
            )
    assert resp.status_code == 200
    assert resp.json()["feedback_action"] == "reject"
    assert enqueue_mock.call_count == 1
    assert enqueue_mock.call_args.args[1] is memory_mock
    assert memory_mock.await_count == 0
