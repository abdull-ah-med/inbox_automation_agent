"""Service tests: apply salutation rewrites draft body and upserts contact."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import DraftNotFoundError
from app.models.schemas.draft import DraftResponseSchema
from app.models.schemas.mailbox_contact import MailboxContactView
from app.repositories.thread_repo import ThreadSchema
from app.services import draft_salutation_service


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes="sales@example.com",
        salute_directory_enabled=True,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def _draft(**overrides: object) -> DraftResponseSchema:
    base = {
        "id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "subject_line": "Re: Quote",
        "reply_body": "Hi Samplecontact,\n\nThanks for reaching out.",
        "teaching_note": "Note",
        "urgency": "NORMAL",
        "urgency_reason": "Routine",
        "suggested_recipients": [],
        "forward_to": None,
        "suggested_actions": [],
        "routing_category": None,
        "edited_body": None,
    }
    base.update(overrides)
    return DraftResponseSchema.model_validate(base)


def _thread(thread_id: uuid.UUID) -> ThreadSchema:
    now = datetime.now(UTC)
    return ThreadSchema(
        id=thread_id,
        mailbox="sales@example.com",
        conversation_id="c1",
        subject="Quote",
        state="AWAITING_ACTION",
        urgency="NORMAL",
        category=None,
        last_message_at=now,
        last_updated_at=now,
    )


@pytest.mark.asyncio
async def test_apply_rewrites_body_and_upserts_contact() -> None:
    session = AsyncMock()
    draft = _draft()
    thread = _thread(draft.thread_id)
    contact = MailboxContactView(
        email="samplecontact@sample-vendor.example.com",
        full_name="Kelvin Collado",
        first_name="Kelvin",
        notes=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    rewritten = draft.model_copy(update={"edited_body": "Hi Kelvin,\n\nThanks for reaching out."})

    with (
        patch(
            "app.services.draft_salutation_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=draft),
        ),
        patch(
            "app.services.draft_salutation_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.draft_salutation_service.mailbox_contact_service.upsert_contact",
            AsyncMock(return_value=(contact, True)),
        ) as upsert,
        patch(
            "app.services.draft_salutation_service.draft_repo.set_edited_body",
            AsyncMock(return_value=rewritten),
        ) as set_body,
    ):
        (
            draft_view,
            addressee,
            saved_contact,
        ) = await draft_salutation_service.apply_draft_salutation(
            session,
            _settings(),
            draft.id,
            email="samplecontact@sample-vendor.example.com",
            first_name="Kelvin",
            full_name="Kelvin Collado",
            notes=None,
            actor_user_id=None,
        )

    assert draft_view.body == "Hi Kelvin,\n\nThanks for reaching out."
    assert addressee.salute_name == "Kelvin"
    assert addressee.directory_hit is True
    assert saved_contact.first_name == "Kelvin"
    upsert.assert_awaited_once()
    set_body.assert_awaited_once()
    assert set_body.await_args.kwargs["edited_body"] == "Hi Kelvin,\n\nThanks for reaching out."


@pytest.mark.asyncio
async def test_apply_missing_draft_raises() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.draft_salutation_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=None),
        ),
        pytest.raises(DraftNotFoundError),
    ):
        await draft_salutation_service.apply_draft_salutation(
            session,
            _settings(),
            uuid.uuid4(),
            email="a@b.com",
            first_name="Ada",
            full_name="",
            notes=None,
            actor_user_id=None,
        )
