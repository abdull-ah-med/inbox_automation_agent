"""Feedback atoms API tests.

Oracle: API contract — exclude endpoint deactivates the atom and returns
the updated record with is_active=False.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_admin, get_current_user
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.repositories.feedback_atom_repo import FeedbackAtomSchema

MAILBOX = "sales@example.com"


def _local_settings() -> Settings:
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


def _atom(*, is_active: bool = True, **overrides) -> FeedbackAtomSchema:
    base = {
        "id": uuid.uuid4(),
        "source_kind": "preference_pair",
        "source_id": uuid.uuid4(),
        "mailbox": MAILBOX,
        "atom_text": "Always acknowledge the driver by name",
        "role": "Fix",
        "applies_when": None,
        "scope": "mailbox",
        "scope_key": MAILBOX,
        "is_active": is_active,
        "hit_count": 0,
        "precision_num": 0,
        "precision_den": 0,
        "expires_at": None,
        "person_bound": False,
        "promoted_from_atom_id": None,
        "created_at": datetime.now(UTC),
    }
    base.update(overrides)
    return FeedbackAtomSchema.model_validate(base)


@pytest.fixture
def app():
    settings = _local_settings()
    get_settings.cache_clear()
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        application = create_app()
        application.dependency_overrides[get_settings] = lambda: settings

        async def fake_user() -> UserMe:
            return UserMe(
                id=uuid.uuid4(),
                email="elise@example.com",
                role="admin",
                created_at=datetime.now(UTC),
            )

        application.dependency_overrides[get_current_user] = fake_user
        application.dependency_overrides[get_current_admin] = fake_user

        mock_session = MagicMock()
        mock_session.commit = AsyncMock()

        async def fake_db():
            yield mock_session

        application.dependency_overrides[get_db] = fake_db
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_list_atoms_returns_200(app) -> None:
    atoms = [_atom(), _atom(id=uuid.uuid4())]
    with patch(
        "app.api.web.feedback_atoms.feedback_atom_repo.list_atoms",
        AsyncMock(return_value=atoms),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/feedback-atoms?mailbox={MAILBOX}")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_list_atoms_requires_mailbox(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/feedback-atoms")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_atoms_unknown_mailbox_returns_404(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/feedback-atoms?mailbox=other@example.com")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_atoms_non_admin_returns_403(app) -> None:
    """Plan §5: atom list is admin UI."""

    async def fake_reviewer() -> UserMe:
        return UserMe(
            id=uuid.uuid4(),
            email="reviewer@example.com",
            role="reviewer",
            created_at=datetime.now(UTC),
        )

    app.dependency_overrides[get_current_user] = fake_reviewer
    app.dependency_overrides.pop(get_current_admin, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/feedback-atoms?mailbox={MAILBOX}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_exclude_atom_deactivates(app) -> None:
    """POST /api/feedback-atoms/{id}/exclude deactivates the atom (is_active=False)."""
    atom_id = uuid.uuid4()
    deactivated = _atom(id=atom_id, is_active=False)

    with (
        patch(
            "app.api.web.feedback_atoms.feedback_atom_repo.get_atom_by_id",
            AsyncMock(return_value=_atom(id=atom_id)),
        ),
        patch(
            "app.api.web.feedback_atoms.feedback_atom_repo.deactivate_atom",
            AsyncMock(return_value=deactivated),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/feedback-atoms/{atom_id}/exclude")

    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_exclude_atom_404_when_not_found(app) -> None:
    with patch(
        "app.api.web.feedback_atoms.feedback_atom_repo.get_atom_by_id",
        AsyncMock(return_value=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/feedback-atoms/{uuid.uuid4()}/exclude")
    assert resp.status_code == 404
