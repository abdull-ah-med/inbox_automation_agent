"""Unit tests for draft repository (mocked async session)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.schemas.draft import DraftSchema, SuggestedRecipientSchema
from app.repositories import draft_repo


def _draft_schema() -> DraftSchema:
    return DraftSchema(
        subject_line="Re: Need docs",
        reply_body="Thanks — attaching the packet.",
        suggested_recipients=[
            SuggestedRecipientSchema(role="reviewer", rationale="Owns the packet"),
        ],
        forward_to=None,
        teaching_note="Vendor asked for the packet; reply with the file.",
        urgency="HIGH",
        urgency_reason="Client waiting on screening docs",
    )


def _orm_row(**overrides: object) -> MagicMock:
    row = MagicMock()
    row.id = uuid.uuid4()
    row.thread_id = uuid.uuid4()
    row.message_id = "m1"
    row.subject = "Re: Need docs"
    row.body = "Thanks — attaching the packet."
    row.recipients = {
        "suggested_recipients": [{"role": "reviewer", "rationale": "Owns the packet"}],
        "forward_to": None,
    }
    row.teaching_note = "Vendor asked for the packet; reply with the file."
    row.urgency = "HIGH"
    row.urgency_reason = "Client waiting on screening docs"
    row.edited_body = None
    row.approved_at = None
    row.rejected_at = None
    row.created_at = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@pytest.mark.asyncio
async def test_create_draft_inserts_and_returns_pydantic() -> None:
    thread_id = uuid.uuid4()
    row = _orm_row(thread_id=thread_id)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)
    session.flush = AsyncMock()

    persisted = await draft_repo.create_draft(
        session,
        thread_id=thread_id,
        message_id="m1",
        draft=_draft_schema(),
        prompt_version="2026-07-21",
    )

    assert persisted.subject_line == "Re: Need docs"
    assert persisted.reply_body == "Thanks — attaching the packet."
    assert persisted.urgency == "HIGH"
    assert persisted.urgency_reason == "Client waiting on screening docs"
    assert persisted.teaching_note.startswith("Vendor asked")
    assert persisted.suggested_recipients[0].role == "reviewer"
    assert "confidence" not in type(persisted).model_fields
    session.execute.assert_awaited_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_draft_conflict_returns_existing() -> None:
    thread_id = uuid.uuid4()
    existing_row = _orm_row(thread_id=thread_id, subject="Existing subject")
    insert_result = MagicMock()
    insert_result.scalar_one_or_none.return_value = None
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = existing_row
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[insert_result, select_result])

    persisted = await draft_repo.create_draft(
        session,
        thread_id=thread_id,
        message_id="m1",
        draft=_draft_schema(),
        prompt_version="v1",
    )

    assert persisted.subject_line == "Existing subject"
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_get_draft_by_message_returns_none_when_missing() -> None:
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)

    assert await draft_repo.get_draft_by_message(session, message_id="missing") is None


@pytest.mark.asyncio
async def test_get_draft_by_message_maps_orm_to_schema() -> None:
    row = _orm_row()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)

    found = await draft_repo.get_draft_by_message(session, message_id="m1")
    assert found is not None
    assert found.urgency == "HIGH"
    assert found.forward_to is None
    assert "confidence" not in type(found).model_fields
