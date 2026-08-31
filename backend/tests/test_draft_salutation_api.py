"""API: apply salutation to draft body without LLM regeneration."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_user
from app.models.schemas.dashboard import DraftView, ReplyAddresseeView
from app.models.schemas.mailbox_contact import MailboxContactView


def _draft_view(**overrides: object) -> DraftView:
    base = {
        "id": uuid.uuid4(),
        "subject": "Re: Quote",
        "body": "Hi Kelvin,\n\nThanks.",
        "forward_to": None,
        "teaching_note": "Note",
        "urgency": "NORMAL",
        "urgency_reason": None,
        "created_at": datetime.now(UTC),
        "suggested_actions": [],
        "approved_at": None,
        "rejected_at": None,
        "edited_body": "Hi Kelvin,\n\nThanks.",
        "feedback_note": None,
        "feedback_action": None,
        "feedback_reason_code": None,
        "routing_category": None,
        "approval_note": None,
        "approval_scope": None,
        "applied_skills": [],
        "tool_calls": None,
    }
    base.update(overrides)
    return DraftView.model_validate(base)


@pytest.mark.asyncio
async def test_apply_salutation_returns_rewritten_body(app) -> None:
    draft_id = uuid.uuid4()
    session = AsyncMock()

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db

    contact = MailboxContactView(
        email="samplecontact@sample-vendor.example.com",
        full_name="Kelvin Collado",
        first_name="Kelvin",
        notes=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    draft = _draft_view(
        id=draft_id,
        body="Hi Kelvin,\n\nThanks.",
        edited_body="Hi Kelvin,\n\nThanks.",
    )
    addressee = ReplyAddresseeView(
        email="samplecontact@sample-vendor.example.com",
        salute_name="Kelvin",
        source="directory",
        source_kind="directory",
        directory_hit=True,
    )

    with patch(
        "app.api.web.drafts.draft_salutation_service.apply_draft_salutation",
        AsyncMock(return_value=(draft, addressee, contact)),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/drafts/{draft_id}/salutation",
                json={
                    "email": "samplecontact@sample-vendor.example.com",
                    "first_name": "Kelvin",
                    "full_name": "Kelvin Collado",
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["body"] == "Hi Kelvin,\n\nThanks."
    assert body["reply_addressee"]["salute_name"] == "Kelvin"
    assert body["reply_addressee"]["directory_hit"] is True
    assert body["contact"]["first_name"] == "Kelvin"


@pytest.mark.asyncio
async def test_apply_salutation_unauthorized_without_bearer(app) -> None:
    app.dependency_overrides.pop(get_current_user, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/drafts/{uuid.uuid4()}/salutation",
            json={"email": "a@b.com", "first_name": "Ada"},
        )
    assert response.status_code == 401
