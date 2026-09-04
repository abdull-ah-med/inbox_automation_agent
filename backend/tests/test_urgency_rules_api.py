"""Urgency rules API tests.

Oracle (plan §5): list by mailbox; pause → paused; resume → active; archive → archived.
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
from app.repositories.urgency_rule_repo import UrgencyRuleSchema

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


def _rule(*, status: str = "active", **overrides) -> UrgencyRuleSchema:
    base = {
        "id": uuid.uuid4(),
        "mailbox": MAILBOX,
        "scope": "sender_domain",
        "scope_key": "domain:statuspage.io",
        "condition": {"sender_domain": "statuspage.io"},
        "action": {"set_urgency_floor": "LOW"},
        "status": status,
        "canary_until": None,
        "activated_at": None,
        "paused_at": None,
        "impact_num": 42,
        "impact_den": 42,
        "precision_num": 40,
        "precision_den": 42,
        "hit_count": 10,
        "override_count": 1,
        "person_bound": False,
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    base.update(overrides)
    return UrgencyRuleSchema.model_validate(base)


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
async def test_list_urgency_rules_returns_200(app) -> None:
    rows = [_rule(), _rule(id=uuid.uuid4(), status="canary")]
    with patch(
        "app.api.web.urgency_rules.urgency_rule_repo.list_urgency_rules_by_mailbox_status",
        AsyncMock(return_value=rows),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/urgency-rules?mailbox={MAILBOX}")
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    assert resp.json()[0]["scope_key"] == "domain:statuspage.io"


@pytest.mark.asyncio
async def test_pause_sets_paused(app) -> None:
    rule_id = uuid.uuid4()
    paused = _rule(id=rule_id, status="paused")
    with (
        patch(
            "app.api.web.urgency_rules.urgency_rule_repo.get_urgency_rule_by_id",
            AsyncMock(return_value=_rule(id=rule_id)),
        ),
        patch(
            "app.services.urgency_rule_service.pause_rule",
            AsyncMock(return_value=paused),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/urgency-rules/{rule_id}/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


@pytest.mark.asyncio
async def test_resume_sets_active(app) -> None:
    rule_id = uuid.uuid4()
    active = _rule(id=rule_id, status="active")
    with (
        patch(
            "app.api.web.urgency_rules.urgency_rule_repo.get_urgency_rule_by_id",
            AsyncMock(return_value=_rule(id=rule_id, status="paused")),
        ),
        patch(
            "app.services.urgency_rule_service.resume_rule",
            AsyncMock(return_value=active),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/urgency-rules/{rule_id}/resume")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_archive_sets_archived(app) -> None:
    rule_id = uuid.uuid4()
    archived = _rule(id=rule_id, status="archived")
    with (
        patch(
            "app.api.web.urgency_rules.urgency_rule_repo.get_urgency_rule_by_id",
            AsyncMock(return_value=_rule(id=rule_id)),
        ),
        patch(
            "app.api.web.urgency_rules.urgency_rule_repo.set_urgency_rule_status",
            AsyncMock(return_value=archived),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/urgency-rules/{rule_id}/archive")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_pause_404_when_missing(app) -> None:
    with patch(
        "app.api.web.urgency_rules.urgency_rule_repo.get_urgency_rule_by_id",
        AsyncMock(return_value=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/urgency-rules/{uuid.uuid4()}/pause")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pause_disallowed_mailbox_returns_404(app) -> None:
    """Pause must 404 when the rule's mailbox is not in target_mailboxes."""
    rule_id = uuid.uuid4()
    foreign = _rule(id=rule_id, mailbox="other@example.com")
    with (
        patch(
            "app.api.web.urgency_rules.urgency_rule_repo.get_urgency_rule_by_id",
            AsyncMock(return_value=foreign),
        ),
        patch(
            "app.services.urgency_rule_service.pause_rule",
            AsyncMock(return_value=foreign),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/urgency-rules/{rule_id}/pause")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Urgency rule not found"


@pytest.mark.asyncio
async def test_resume_disallowed_mailbox_returns_404(app) -> None:
    rule_id = uuid.uuid4()
    foreign = _rule(id=rule_id, mailbox="other@example.com", status="paused")
    with (
        patch(
            "app.api.web.urgency_rules.urgency_rule_repo.get_urgency_rule_by_id",
            AsyncMock(return_value=foreign),
        ),
        patch(
            "app.services.urgency_rule_service.resume_rule",
            AsyncMock(return_value=foreign),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/urgency-rules/{rule_id}/resume")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Urgency rule not found"


@pytest.mark.asyncio
async def test_archive_disallowed_mailbox_returns_404(app) -> None:
    rule_id = uuid.uuid4()
    foreign = _rule(id=rule_id, mailbox="other@example.com")
    with (
        patch(
            "app.api.web.urgency_rules.urgency_rule_repo.get_urgency_rule_by_id",
            AsyncMock(return_value=foreign),
        ),
        patch(
            "app.api.web.urgency_rules.urgency_rule_repo.set_urgency_rule_status",
            AsyncMock(return_value=foreign),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/urgency-rules/{rule_id}/archive")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Urgency rule not found"
