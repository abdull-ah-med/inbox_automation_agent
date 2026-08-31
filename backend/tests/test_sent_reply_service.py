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


OUTLOOK_QUOTED_TAIL = (
    "From: Alice <alice@example.com>\n"
    "Sent: Monday, August 22, 2022 10:43 AM\n"
    "To: elise@example.com\n"
    "Subject: Re: Deploy\n\n"
    "Can you check the deploy?"
)


def _message(
    *,
    thread_id: uuid.UUID,
    received_at: datetime | None = None,
    body_text: str = "Sent body text",
    body_preview: str | None = "Sent body",
    unique_body_text: str | None = None,
    meeting_message_type: str | None = None,
    meeting_response_type: str | None = None,
) -> MessageSchema:
    return MessageSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id="AAMkAG-sent-001",
        direction="outbound",
        sender="elise@example.com",
        body_text=body_text,
        body_preview=body_preview,
        unique_body_text=unique_body_text,
        received_at=received_at or datetime.now(UTC),
        to_recipients=["client@example.com"],
        cc_recipients=[],
        meeting_message_type=meeting_message_type,
        meeting_response_type=meeting_response_type,
    )


def _this_outbound_is_tip(message: MessageSchema) -> object:
    return patch(
        "app.services.sent_reply_service.message_repo.list_by_thread",
        AsyncMock(return_value=[message]),
    )


async def _resolve_capturing_snapshot(message: MessageSchema) -> str:
    """Call resolve with stubs; return the snapshot passed to insert."""
    thread_id = message.thread_id
    session = AsyncMock()
    created = _sent_reply(thread_id=thread_id, message_id=message.id)
    with (
        _this_outbound_is_tip(message),
        patch(
            "app.services.sent_reply_service.draft_repo.list_by_thread",
            AsyncMock(return_value=[]),
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
    return insert_mock.await_args.kwargs["sent_body_snapshot"]


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
        _this_outbound_is_tip(message),
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
            AsyncMock(return_value=SimpleNamespace(urgency="HIGH", id=thread_id)),
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
        _this_outbound_is_tip(message),
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
        _this_outbound_is_tip(message),
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


@pytest.mark.asyncio
async def test_snapshot_prefers_unique_body_over_full_quote_wall() -> None:
    """Sent reply panel must store the new reply, not Outlook quoted history."""
    thread_id = uuid.uuid4()
    full_body = f"Will do.\n\n{OUTLOOK_QUOTED_TAIL}"
    message = _message(
        thread_id=thread_id,
        body_text=full_body,
        body_preview="Will do.",
        unique_body_text="Will do.",
    )

    snapshot = await _resolve_capturing_snapshot(message)

    assert snapshot == "Will do."
    assert "From: Alice" not in snapshot


@pytest.mark.asyncio
async def test_snapshot_quote_splits_when_unique_body_is_null() -> None:
    """Legacy rows without unique_body_text fall back to quote-split of body_text."""
    thread_id = uuid.uuid4()
    message = _message(
        thread_id=thread_id,
        body_text=f"Thanks.\n\n{OUTLOOK_QUOTED_TAIL}",
        body_preview="Thanks.",
        unique_body_text=None,
    )

    snapshot = await _resolve_capturing_snapshot(message)

    assert snapshot == "Thanks."
    assert "From: Alice" not in snapshot


@pytest.mark.asyncio
async def test_snapshot_empty_when_no_reply_text() -> None:
    """Truly empty Graph bodies stay empty — quote-only / blank is not invented."""
    thread_id = uuid.uuid4()
    message = _message(
        thread_id=thread_id,
        body_text="",
        body_preview=None,
        unique_body_text="",
    )

    snapshot = await _resolve_capturing_snapshot(message)

    assert snapshot == ""


@pytest.mark.asyncio
async def test_meeting_accepted_resolves_thread() -> None:
    """Elise RSVP'd in Calendar — that is the tip action, so the thread closes."""
    thread_id = uuid.uuid4()
    message = _message(
        thread_id=thread_id,
        body_text="",
        body_preview=None,
        unique_body_text="",
        meeting_message_type="meetingAccepted",
        meeting_response_type="accepted",
    )
    session = AsyncMock()
    created = _sent_reply(
        thread_id=thread_id,
        message_id=message.id,
        draft_id=None,
        sent_body_snapshot="",
        matched_by="time_window",
    )

    with (
        _this_outbound_is_tip(message),
        patch(
            "app.services.sent_reply_service.draft_repo.list_by_thread",
            AsyncMock(return_value=[]),
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
            AsyncMock(return_value=SimpleNamespace(urgency="NORMAL", id=thread_id)),
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
            conversation_id="conv-meeting-1",
            mailbox="elise@example.com",
        )

    assert result is not None
    insert_kwargs = insert_mock.await_args.kwargs
    assert insert_kwargs["draft_id"] is None
    assert insert_kwargs["sent_body_snapshot"] == ""
    outcome_mock.assert_awaited_once()
    assert outcome_mock.await_args.kwargs["state"] == "RESOLVED"
    assert audit_mock.await_args.kwargs["event_type"] == "thread.resolved.sent_reply_detected"


@pytest.mark.asyncio
async def test_resolve_does_not_close_thread_when_newer_inbound_exists() -> None:
    """Sent Items copy of Elise's Aug 27 send must not RESOLVE Ruth's Aug 28 follow-up.

    Graph assigns a new id in Sent Items, so this is a fresh sent_replies insert,
    not a Redis-heal duplicate. Closing is illegal while the tip is inbound.
    """
    thread_id = uuid.uuid4()
    sent_at = datetime(2026, 8, 27, 14, 23, 19, tzinfo=UTC)
    inbound_at = datetime(2026, 8, 28, 17, 16, 10, tzinfo=UTC)
    outbound = _message(thread_id=thread_id, received_at=sent_at)
    inbound = MessageSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id="AAMkAG-ruth-followup",
        direction="inbound",
        sender="ruth.hooker@sample-lab-vendor.example.com",
        body_text="Following up on the integration",
        received_at=inbound_at,
        to_recipients=["sampleagent@sample-site.example.com"],
        cc_recipients=[],
    )
    session = AsyncMock()
    created = _sent_reply(thread_id=thread_id, message_id=outbound.id)
    outcome_mock = AsyncMock()

    with (
        patch(
            "app.services.sent_reply_service.message_repo.list_by_thread",
            AsyncMock(return_value=[outbound, inbound]),
        ),
        patch(
            "app.services.sent_reply_service.draft_repo.list_by_thread",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.sent_reply_service.sent_reply_repo.insert_sent_reply",
            AsyncMock(return_value=(created, True)),
        ) as insert_mock,
        patch(
            "app.services.sent_reply_service.thread_repo.set_thread_outcome",
            outcome_mock,
        ),
        patch(
            "app.services.sent_reply_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await sent_reply_service.resolve_thread_from_outbound(
            session,
            thread_id=thread_id,
            message=outbound,
            conversation_id="conv-ruth-followup",
            mailbox="sampleagent@sample-site.example.com",
        )

    assert result is not None
    assert insert_mock.await_count == 1
    outcome_mock.assert_not_awaited()
    audit_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_meeting_accepted_does_not_link_unapproved_letter() -> None:
    """Calendar accept is not the drafted RSVP email — do not time-window match it."""
    thread_id = uuid.uuid4()
    draft = _draft(thread_id=thread_id)
    message = _message(
        thread_id=thread_id,
        body_text="",
        unique_body_text="",
        meeting_message_type="meetingAccepted",
        meeting_response_type="accepted",
    )
    session = AsyncMock()
    created = _sent_reply(thread_id=thread_id, message_id=message.id, draft_id=None)

    with (
        _this_outbound_is_tip(message),
        patch(
            "app.services.sent_reply_service.draft_repo.list_by_thread",
            AsyncMock(return_value=[draft]),
        ) as drafts_mock,
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
            AsyncMock(return_value=SimpleNamespace(urgency=None, id=thread_id)),
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
            conversation_id="conv-meeting-2",
            mailbox="elise@example.com",
        )

    drafts_mock.assert_not_awaited()
    assert insert_mock.await_args.kwargs["draft_id"] is None


@pytest.mark.asyncio
async def test_meeting_cancelled_outbound_resolves_thread() -> None:
    thread_id = uuid.uuid4()
    message = _message(
        thread_id=thread_id,
        body_text="",
        unique_body_text="",
        meeting_message_type="meetingCancelled",
    )
    session = AsyncMock()
    created = _sent_reply(thread_id=thread_id, message_id=message.id, draft_id=None)

    with (
        _this_outbound_is_tip(message),
        patch(
            "app.services.sent_reply_service.sent_reply_repo.insert_sent_reply",
            AsyncMock(return_value=(created, True)),
        ),
        patch(
            "app.services.sent_reply_service.thread_repo.set_thread_outcome",
            AsyncMock(return_value=object()),
        ) as outcome_mock,
        patch(
            "app.services.sent_reply_service.thread_repo.get_by_id",
            AsyncMock(return_value=SimpleNamespace(urgency=None, id=thread_id)),
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
            conversation_id="conv-cancel-1",
            mailbox="elise@example.com",
        )

    assert result is not None
    assert outcome_mock.await_args.kwargs["state"] == "RESOLVED"


@pytest.mark.asyncio
async def test_thread_tip_already_replied_true_when_tip_is_resolved_outbound() -> None:
    thread_id = uuid.uuid4()
    outbound_id = uuid.uuid4()
    sent_at = datetime(2026, 8, 27, 19, 0, tzinfo=UTC)
    messages = [
        MessageSchema(
            id=uuid.uuid4(),
            thread_id=thread_id,
            graph_message_id="in-1",
            direction="inbound",
            sender="client@example.com",
            body_text="Question",
            received_at=datetime(2026, 8, 27, 18, 0, tzinfo=UTC),
            to_recipients=["elise@example.com"],
            cc_recipients=[],
        ),
        MessageSchema(
            id=outbound_id,
            thread_id=thread_id,
            graph_message_id="out-1",
            direction="outbound",
            sender="elise@example.com",
            body_text="Answer",
            received_at=sent_at,
            to_recipients=["client@example.com"],
            cc_recipients=[],
        ),
    ]
    sent = SentReplySchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        message_id=outbound_id,
        draft_id=None,
        sent_body_snapshot="Answer",
        sent_at=sent_at,
        matched_by="time_window",
        created_at=sent_at,
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.sent_reply_service.message_repo.list_by_thread",
            AsyncMock(return_value=messages),
        ),
        patch(
            "app.services.sent_reply_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=sent),
        ),
    ):
        assert await sent_reply_service.thread_tip_already_replied(session, thread_id) is True


@pytest.mark.asyncio
async def test_thread_tip_already_replied_false_when_newer_inbound_after_send() -> None:
    thread_id = uuid.uuid4()
    outbound_id = uuid.uuid4()
    sent_at = datetime(2026, 8, 27, 19, 0, tzinfo=UTC)
    messages = [
        MessageSchema(
            id=outbound_id,
            thread_id=thread_id,
            graph_message_id="out-1",
            direction="outbound",
            sender="elise@example.com",
            body_text="Answer",
            received_at=sent_at,
            to_recipients=["client@example.com"],
            cc_recipients=[],
        ),
        MessageSchema(
            id=uuid.uuid4(),
            thread_id=thread_id,
            graph_message_id="in-2",
            direction="inbound",
            sender="beau@example.com",
            body_text="Follow-up answers",
            received_at=datetime(2026, 8, 28, 0, 25, tzinfo=UTC),
            to_recipients=["elise@example.com"],
            cc_recipients=[],
        ),
    ]
    sent = SentReplySchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        message_id=outbound_id,
        draft_id=None,
        sent_body_snapshot="Answer",
        sent_at=sent_at,
        matched_by="time_window",
        created_at=sent_at,
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.sent_reply_service.message_repo.list_by_thread",
            AsyncMock(return_value=messages),
        ),
        patch(
            "app.services.sent_reply_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=sent),
        ),
    ):
        assert await sent_reply_service.thread_tip_already_replied(session, thread_id) is False
