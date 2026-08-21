"""Unit tests for sent_reply_service matching and resolve."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.schemas.draft import DraftResponseSchema
from app.repositories.message_repo import MessageSchema
from app.repositories.sent_reply_repo import SentReplySchema
from app.services import sent_reply_service


def _draft(**overrides: object) -> DraftResponseSchema:
    base = {
        "id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "created_at": datetime.now(UTC) - timedelta(hours=1),
        "subject_line": "Re: Test",
        "reply_body": "Proposed body",
        "teaching_note": "Note",
        "urgency": "NORMAL",
        "urgency_reason": "Routine",
        "suggested_recipients": [],
        "forward_to": None,
        "suggested_actions": [],
    }
    base.update(overrides)
    return DraftResponseSchema.model_validate(base)


def _message(*, thread_id: uuid.UUID, received_at: datetime | None = None) -> MessageSchema:
    return MessageSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id="AAMkAG-sent-001",
        direction="outbound",
        sender="elise@example.com",
        body_text="Sent body text",
        body_preview="Sent body",
        received_at=received_at or datetime.now(UTC),
        to_recipients=["client@example.com"],
        cc_recipients=[],
    )


def _sent_reply(**overrides: object) -> SentReplySchema:
    base = {
        "id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "message_id": uuid.uuid4(),
        "draft_id": None,
        "sent_body_snapshot": "Sent body text",
        "sent_at": datetime.now(UTC),
        "matched_by": "approved_draft",
        "created_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SentReplySchema.model_validate(base)


@pytest.mark.asyncio
async def test_resolve_matches_approved_draft() -> None:
    thread_id = uuid.uuid4()
    approved_at = datetime.now(UTC) - timedelta(minutes=30)
    draft = _draft(thread_id=thread_id, approved_at=approved_at)
    message = _message(thread_id=thread_id)
    session = AsyncMock()
    created = _sent_reply(
        thread_id=thread_id,
        message_id=message.id,
        draft_id=draft.id,
        matched_by="approved_draft",
    )

    with (
        patch(
            "app.services.sent_reply_service.draft_repo.list_by_thread",
            AsyncMock(return_value=[draft]),
        ),
        patch(
            "app.services.sent_reply_service.sent_reply_repo.get_by_draft",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.sent_reply_service.sent_reply_repo.insert_sent_reply",
            AsyncMock(return_value=(created, True)),
        ) as insert_mock,
        patch(
            "app.services.sent_reply_service.thread_repo.set_thread_outcome",
            AsyncMock(return_value=object()),
        ) as outcome_mock,
        patch(
            "app.services.sent_reply_service.thread_repo.get_by_id",
            AsyncMock(
                return_value=SimpleNamespace(urgency="HIGH", id=thread_id)
            ),
        ),
        patch(
            "app.services.sent_reply_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await sent_reply_service.resolve_thread_from_outbound(
            session,
            thread_id=thread_id,
            message=message,
            conversation_id="conv-1",
            mailbox="elise@example.com",
        )

    assert result is not None
    assert result.matched_by == "approved_draft"
    insert_kwargs = insert_mock.await_args.kwargs
    assert insert_kwargs["draft_id"] == draft.id
    assert insert_kwargs["matched_by"] == "approved_draft"
    outcome_mock.assert_awaited_once()
    assert outcome_mock.await_args.kwargs["state"] == "RESOLVED"
    payload = audit_mock.await_args.kwargs["payload"]
    assert payload["matched_by"] == "approved_draft"
    assert payload["message_id"] == str(message.id)
    assert payload["human"]["title"] == "Resolved from sent reply"
    assert "sent_body" not in payload
    assert "body" not in payload


@pytest.mark.asyncio
async def test_resolve_falls_back_to_time_window() -> None:
    thread_id = uuid.uuid4()
    draft = _draft(
        thread_id=thread_id,
        approved_at=None,
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    message = _message(thread_id=thread_id)
    session = AsyncMock()
    created = _sent_reply(
        thread_id=thread_id,
        message_id=message.id,
        draft_id=draft.id,
        matched_by="time_window",
    )

    with (
        patch(
            "app.services.sent_reply_service.draft_repo.list_by_thread",
            AsyncMock(return_value=[draft]),
        ),
        patch(
            "app.services.sent_reply_service.sent_reply_repo.insert_sent_reply",
            AsyncMock(return_value=(created, True)),
        ) as insert_mock,
        patch(
            "app.services.sent_reply_service.thread_repo.set_thread_outcome",
            AsyncMock(return_value=object()),
        ),
        patch(
            "app.services.sent_reply_service.thread_repo.get_by_id",
            AsyncMock(return_value=SimpleNamespace(urgency="NORMAL", id=thread_id)),
        ),
        patch(
            "app.services.sent_reply_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        result = await sent_reply_service.resolve_thread_from_outbound(
            session,
            thread_id=thread_id,
            message=message,
            conversation_id="conv-1",
            mailbox="elise@example.com",
        )

    assert result is not None
    assert insert_mock.await_args.kwargs["matched_by"] == "time_window"
    assert insert_mock.await_args.kwargs["draft_id"] == draft.id


@pytest.mark.asyncio
async def test_resolve_idempotent_skips_second_transition() -> None:
    thread_id = uuid.uuid4()
    message = _message(thread_id=thread_id)
    session = AsyncMock()
    existing = _sent_reply(thread_id=thread_id, message_id=message.id)

    with (
        patch(
            "app.services.sent_reply_service.draft_repo.list_by_thread",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.sent_reply_service.sent_reply_repo.insert_sent_reply",
            AsyncMock(return_value=(existing, False)),
        ),
        patch(
            "app.services.sent_reply_service.thread_repo.set_thread_outcome",
            AsyncMock(),
        ) as outcome_mock,
        patch(
            "app.services.sent_reply_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await sent_reply_service.resolve_thread_from_outbound(
            session,
            thread_id=thread_id,
            message=message,
            conversation_id="conv-1",
            mailbox="elise@example.com",
        )

    assert result is existing
    outcome_mock.assert_not_awaited()
    audit_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_skips_approved_draft_already_linked() -> None:
    thread_id = uuid.uuid4()
    approved = _draft(
        thread_id=thread_id,
        approved_at=datetime.now(UTC) - timedelta(hours=1),
    )
    recent = _draft(
        thread_id=thread_id,
        approved_at=None,
        created_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    message = _message(thread_id=thread_id)
    session = AsyncMock()
    created = _sent_reply(
        thread_id=thread_id,
        message_id=message.id,
        draft_id=recent.id,
        matched_by="time_window",
    )

    with (
        patch(
            "app.services.sent_reply_service.draft_repo.list_by_thread",
            AsyncMock(return_value=[recent, approved]),
        ),
        patch(
            "app.services.sent_reply_service.sent_reply_repo.get_by_draft",
            AsyncMock(return_value=_sent_reply(draft_id=approved.id)),
        ),
        patch(
            "app.services.sent_reply_service.sent_reply_repo.insert_sent_reply",
            AsyncMock(return_value=(created, True)),
        ) as insert_mock,
        patch(
            "app.services.sent_reply_service.thread_repo.set_thread_outcome",
            AsyncMock(return_value=object()),
        ),
        patch(
            "app.services.sent_reply_service.thread_repo.get_by_id",
            AsyncMock(return_value=SimpleNamespace(urgency="NORMAL", id=thread_id)),
        ),
        patch(
            "app.services.sent_reply_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        await sent_reply_service.resolve_thread_from_outbound(
            session,
            thread_id=thread_id,
            message=message,
            conversation_id="conv-1",
            mailbox="elise@example.com",
        )

    assert insert_mock.await_args.kwargs["matched_by"] == "time_window"
    assert insert_mock.await_args.kwargs["draft_id"] == recent.id


def test_cap_body_truncates_at_20k() -> None:
    long_body = "x" * 25_000
    capped = sent_reply_service._cap_body(long_body)
    assert len(capped) == 20_000
