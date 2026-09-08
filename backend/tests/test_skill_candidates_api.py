"""Skill candidates API — mailbox allowlist on list and admin actions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import get_db
from app.models.schemas.skill_candidate import SkillCandidateResponseSchema
from tests.api_fixtures import make_local_settings

ALLOWED = "sales@example.com"


@pytest.fixture
def local_settings():
    return make_local_settings(target_mailboxes=ALLOWED)


@pytest.fixture
def api_user_role() -> str:
    return "admin"


def _candidate(**overrides: object) -> SkillCandidateResponseSchema:
    base = {
        "id": uuid.uuid4(),
        "mailbox": ALLOWED,
        "routing_category": "billing",
        "reason_code": "wrong_tone",
        "proposed_name": "No same-day promises",
        "proposed_content": "Never promise same-day turnaround.",
        "source_rejection_ids": [],
        "status": "pending",
        "created_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SkillCandidateResponseSchema.model_validate(base)


@pytest.mark.asyncio
async def test_list_skill_candidates_disallowed_mailbox_returns_404(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/skill-candidates?mailbox=other@example.com")
    assert response.status_code == 404
    assert response.json()["detail"] == "Mailbox not found"


@pytest.mark.asyncio
async def test_list_skill_candidates_without_mailbox_scopes_to_allowed(app) -> None:
    with patch(
        "app.api.web.skill_candidates.skill_candidate_service.list_pending",
        AsyncMock(return_value=[]),
    ) as list_mock:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/skill-candidates")
    assert response.status_code == 200
    list_mock.assert_awaited_once()
    assert list_mock.await_args.kwargs["mailboxes"] == [ALLOWED]
    assert list_mock.await_args.kwargs["mailbox"] is None


@pytest.mark.asyncio
async def test_accept_skill_candidate_disallowed_mailbox_returns_404(app) -> None:
    candidate_id = uuid.uuid4()
    foreign = _candidate(id=candidate_id, mailbox="other@example.com")
    session = MagicMock()
    session.commit = AsyncMock()

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db

    with (
        patch(
            "app.services.skill_candidate_service.get_candidate",
            AsyncMock(return_value=foreign),
        ),
        patch(
            "app.api.web.skill_candidates.skill_candidate_service.accept_candidate",
            AsyncMock(),
        ) as accept_mock,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/api/skill-candidates/{candidate_id}/accept")
    assert response.status_code == 404
    accept_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dismiss_skill_candidate_disallowed_mailbox_returns_404(app) -> None:
    candidate_id = uuid.uuid4()
    foreign = _candidate(id=candidate_id, mailbox="other@example.com")
    session = MagicMock()
    session.commit = AsyncMock()

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db

    with (
        patch(
            "app.services.skill_candidate_service.get_candidate",
            AsyncMock(return_value=foreign),
        ),
        patch(
            "app.api.web.skill_candidates.skill_candidate_service.dismiss_candidate",
            AsyncMock(),
        ) as dismiss_mock,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/api/skill-candidates/{candidate_id}/dismiss")
    assert response.status_code == 404
    dismiss_mock.assert_not_awaited()
