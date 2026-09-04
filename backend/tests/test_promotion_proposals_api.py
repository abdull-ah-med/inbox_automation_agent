"""Promotion proposals API tests.

Oracle (plan §5): accept/revert must call promotion_service (golden-set gate,
canary rule create, chat-cache invalidate). Dismiss is status-only.
Gate failure → 400 with the service error message.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_admin, get_current_user
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.repositories.promotion_proposal_repo import PromotionProposalSchema

MAILBOX = "sales@example.com"
FOREIGN_MAILBOX = "other@example.com"


def _found(proposal: PromotionProposalSchema):
    return patch(
        "app.api.web.promotion_proposals.promotion_proposal_repo.get_proposal_by_id",
        AsyncMock(return_value=proposal),
    )


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


def _proposal(*, status: str = "pending", **overrides) -> PromotionProposalSchema:
    base = {
        "id": uuid.uuid4(),
        "mailbox": MAILBOX,
        "kind": "urgency_rule",
        "payload": {
            "condition": {"sender_domain": "statuspage.io"},
            "action": {"set_urgency_floor": "LOW"},
        },
        "impact_num": 42,
        "impact_den": 42,
        "precision_num": 42,
        "precision_den": 42,
        "evidence_ids": [],
        "status": status,
        "expires_at": datetime.now(UTC) + timedelta(days=30),
        "created_at": datetime.now(UTC),
    }
    base.update(overrides)
    return PromotionProposalSchema.model_validate(base)


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
async def test_list_pending_proposals_returns_200(app) -> None:
    rows = [_proposal(), _proposal(id=uuid.uuid4())]
    with patch(
        "app.api.web.promotion_proposals.promotion_proposal_repo.list_proposals",
        AsyncMock(return_value=rows),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/promotion-proposals?status=pending")
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    assert resp.json()[0]["impact_num"] == 42


@pytest.mark.asyncio
async def test_accept_calls_promotion_service(app) -> None:
    """Accept must go through promotion_service (gates + canary create), not repo alone."""
    proposal_id = uuid.uuid4()
    accepted = _proposal(id=proposal_id, status="accepted")

    with (
        _found(_proposal(id=proposal_id)),
        patch(
            "app.services.promotion_service.accept_proposal",
            AsyncMock(return_value=accepted),
        ) as mock_accept,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/promotion-proposals/{proposal_id}/accept")

    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    mock_accept.assert_awaited_once()


@pytest.mark.asyncio
async def test_accept_high_change_rate_returns_409(app) -> None:
    from app.services.promotion_service import HighChangeRateError

    proposal_id = uuid.uuid4()
    with (
        _found(_proposal(id=proposal_id)),
        patch(
            "app.services.promotion_service.accept_proposal",
            AsyncMock(side_effect=HighChangeRateError(proposal_id, 0.45)),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/promotion-proposals/{proposal_id}/accept")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "high_change_rate"
    assert detail["change_rate"] == 0.45


@pytest.mark.asyncio
async def test_accept_gate_failure_returns_400(app) -> None:
    """Golden-set / replay failure from the service surfaces as HTTP 400."""
    proposal_id = uuid.uuid4()

    with (
        _found(_proposal(id=proposal_id)),
        patch(
            "app.services.promotion_service.accept_proposal",
            AsyncMock(side_effect=ValueError("failed the golden-set gate")),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/promotion-proposals/{proposal_id}/accept")

    assert resp.status_code == 400
    assert "golden-set" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_accept_not_pending_returns_409(app) -> None:
    """Wrong-state accept is a conflict, not a malformed request."""
    from app.core.exceptions import ProposalConflictError

    proposal_id = uuid.uuid4()
    with (
        _found(_proposal(id=proposal_id, status="accepted")),
        patch(
            "app.services.promotion_service.accept_proposal",
            AsyncMock(side_effect=ProposalConflictError("Proposal is not pending")),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/promotion-proposals/{proposal_id}/accept")

    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"] == "Proposal is not in the required state"
    assert body["error_type"] == "ProposalConflictError"


@pytest.mark.asyncio
async def test_accept_missing_proposal_returns_404(app) -> None:
    """A vanished row after the allowlist check is not-found, not 400."""
    from app.core.exceptions import ProposalNotFoundError

    proposal_id = uuid.uuid4()
    with (
        _found(_proposal(id=proposal_id)),
        patch(
            "app.services.promotion_service.accept_proposal",
            AsyncMock(side_effect=ProposalNotFoundError("Proposal not found")),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/promotion-proposals/{proposal_id}/accept")

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "Proposal not found"
    assert body["error_type"] == "ProposalNotFoundError"


@pytest.mark.asyncio
async def test_dismiss_calls_promotion_service(app) -> None:
    proposal_id = uuid.uuid4()
    dismissed = _proposal(id=proposal_id, status="dismissed")

    with (
        _found(_proposal(id=proposal_id)),
        patch(
            "app.services.promotion_service.dismiss_proposal",
            AsyncMock(return_value=dismissed),
        ) as mock_dismiss,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/promotion-proposals/{proposal_id}/dismiss")

    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"
    mock_dismiss.assert_awaited_once()


@pytest.mark.asyncio
async def test_revert_calls_promotion_service(app) -> None:
    """Revert must call promotion_service.revert_promotion (archive rule + cache)."""
    proposal_id = uuid.uuid4()
    reverted = _proposal(id=proposal_id, status="archived")

    with (
        _found(_proposal(id=proposal_id, status="accepted")),
        patch(
            "app.services.promotion_service.revert_promotion",
            AsyncMock(return_value=reverted),
        ) as mock_revert,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/promotion-proposals/{proposal_id}/revert")

    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"
    mock_revert.assert_awaited_once()


@pytest.mark.asyncio
async def test_revert_not_accepted_returns_409(app) -> None:
    from app.core.exceptions import ProposalConflictError

    proposal_id = uuid.uuid4()

    with (
        _found(_proposal(id=proposal_id)),
        patch(
            "app.services.promotion_service.revert_promotion",
            AsyncMock(side_effect=ProposalConflictError("is not accepted")),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/promotion-proposals/{proposal_id}/revert")

    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"] == "Proposal is not in the required state"
    assert body["error_type"] == "ProposalConflictError"


@pytest.mark.asyncio
async def test_accept_foreign_mailbox_returns_404(app) -> None:
    """Mutate-by-id must not accept a proposal whose mailbox is outside the allowlist."""
    proposal_id = uuid.uuid4()
    foreign = _proposal(id=proposal_id, mailbox=FOREIGN_MAILBOX)

    with (
        _found(foreign),
        patch(
            "app.services.promotion_service.accept_proposal",
            AsyncMock(),
        ) as mock_accept,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/promotion-proposals/{proposal_id}/accept")

    assert resp.status_code == 404
    mock_accept.assert_not_awaited()
