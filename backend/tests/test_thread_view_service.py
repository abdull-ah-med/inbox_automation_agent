"""Unit tests for mailbox key inference and dashboard service assembly."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.mailbox_keys import infer_mailbox_key, resolve_mailbox_email
from app.models.schemas.dashboard import AuditEntry, ThreadSummary
from app.services import dashboard_service, thread_view_service


def test_infer_mailbox_key() -> None:
    assert infer_mailbox_key("clientrelations@example.com") == "client-relations"
    assert infer_mailbox_key("sales@example.com") == "sales"
    assert infer_mailbox_key("vendors@example.com") == "vendor"
    assert infer_mailbox_key("intermediary@example.com") == "intermediary"
    assert infer_mailbox_key("inquiries@sample-site.example.com") == "inquiries"
    assert infer_mailbox_key("support@sample-site.example.com") == "support"


def test_resolve_mailbox_email() -> None:
    emails = [
        "clientrelations@example.com",
        "sales@example.com",
        "vendors@example.com",
        "intermediary@example.com",
        "inquiries@sample-site.example.com",
    ]
    assert resolve_mailbox_email("sales", emails) == "sales@example.com"
    assert resolve_mailbox_email("inquiries", emails) == "inquiries@sample-site.example.com"
    assert resolve_mailbox_email("missing", emails) is None


@pytest.mark.asyncio
async def test_dashboard_overview_assembles_configured_mailboxes() -> None:
    settings = Settings(
        environment="local",
        target_mailboxes=(
            "inquiries@example.com,support@example.com,info@example.com,sampleagent@example.com"
        ),
        staleness_threshold_hours=24,
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.dashboard_service.thread_repo.aggregate_overview",
            AsyncMock(
                return_value=[
                    {
                        "mailbox": "inquiries@example.com",
                        "thread_count": 3,
                        "awaiting_action_count": 1,
                        "stale_count": 0,
                        "urgency_breakdown": {"HIGH": 1, "NORMAL": 2, "CRITICAL": 0, "LOW": 0},
                    }
                ]
            ),
        ),
        patch(
            "app.services.dashboard_service.thread_repo.list_recent_for_mailboxes",
            AsyncMock(return_value={}),
        ),
        patch(
            "app.services.dashboard_service.thread_repo.list_needs_attention",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.dashboard_service.thread_repo.list_open_fyi",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.dashboard_service.thread_repo.list_recently_resolved_by_draftassistant",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.dashboard_service.audit_repo.list_recent",
            AsyncMock(
                return_value=[
                    AuditEntry(
                        timestamp=datetime.now(UTC),
                        event="email_received",
                        detail="test",
                        source="system",
                    )
                ]
            ),
        ),
    ):
        overview = await dashboard_service.get_overview(session, settings)

    assert len(overview.mailboxes) == 4
    inquiries = next(m for m in overview.mailboxes if m.mailbox == "inquiries")
    assert inquiries.thread_count == 3
    assert inquiries.email_address == "inquiries@example.com"
    assert overview.total_threads == 3
    assert len(overview.recent_activity) == 1


@pytest.mark.asyncio
async def test_thread_view_404_when_missing() -> None:
    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
        salute_directory_enabled=False,
    )
    session = AsyncMock()
    with patch(
        "app.services.thread_view_service.thread_repo.get_by_id",
        AsyncMock(return_value=None),
    ):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await thread_view_service.get_thread_detail(session, settings, uuid.uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_thread_view_assembles_detail() -> None:
    from app.repositories.message_repo import MessageSchema
    from app.repositories.thread_repo import ThreadSchema

    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
        salute_directory_enabled=False,
    )
    thread_id = uuid.uuid4()
    message_id = uuid.uuid4()
    now = datetime.now(UTC)
    thread = ThreadSchema(
        id=thread_id,
        mailbox="sales@example.com",
        conversation_id="conv-1",
        subject="Hello",
        state="NEW",
        urgency="HIGH",
        category=None,
        last_message_at=now,
        last_updated_at=now,
    )
    summary = ThreadSummary(
        id=thread_id,
        mailbox="sales@example.com",
        mailbox_key="sales",
        subject="Hello",
        state="NEW",
        urgency="HIGH",
        last_message_at=now,
        last_sender="a@b.com",
        preview="hi",
        staleness_hours=0,
        message_count=1,
        outlook_url="https://outlook.office365.com/owa/?ItemID=AAMkAG-msg-001&exvsurl=1&viewmodel=ReadMessageItem",
    )
    message = MessageSchema(
        id=message_id,
        thread_id=thread_id,
        graph_message_id="AAMkAG-msg-001",
        direction="inbound",
        sender="a@b.com",
        body_text="hi",
        body_preview="hi",
        received_at=now,
        to_recipients=["sales@example.com"],
        cc_recipients=[],
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.thread_view_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.thread_view_service.thread_repo.build_thread_summary",
            AsyncMock(return_value=summary),
        ),
        patch(
            "app.services.thread_view_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.thread_view_service.classification_repo.get_latest_for_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.draft_repo.get_latest_proposed_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_by_thread_id",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_raw_by_conversation",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.get_latest_triage_flags",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.related_thread_service.list_stored_associations",
            AsyncMock(return_value=[]),
        ),
    ):
        detail = await thread_view_service.get_thread_detail(session, settings, thread_id)
    assert detail.thread.subject == "Hello"
    assert detail.thread.outlook_url is not None
    assert len(detail.messages) == 1
    assert detail.messages[0].outlook_url is not None
    assert "outlook.office365.com/owa/" in detail.messages[0].outlook_url
    assert "ItemID=" in detail.messages[0].outlook_url
    assert detail.classification is None
    assert detail.sent_reply is None
    assert detail.draft_vs_sent_diff is None


@pytest.mark.asyncio
async def test_thread_view_includes_sent_reply_and_diff() -> None:
    from app.models.schemas.draft import DraftResponseSchema
    from app.repositories.message_repo import MessageSchema
    from app.repositories.sent_reply_repo import SentReplySchema
    from app.repositories.thread_repo import ThreadSchema

    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
        salute_directory_enabled=False,
    )
    thread_id = uuid.uuid4()
    message_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    now = datetime.now(UTC)
    thread = ThreadSchema(
        id=thread_id,
        mailbox="sales@example.com",
        conversation_id="conv-1",
        subject="Hello",
        state="RESOLVED",
        urgency="HIGH",
        category=None,
        last_message_at=now,
        last_updated_at=now,
    )
    summary = ThreadSummary(
        id=thread_id,
        mailbox="sales@example.com",
        mailbox_key="sales",
        subject="Hello",
        state="RESOLVED",
        urgency="HIGH",
        last_message_at=now,
        last_sender="sales@example.com",
        preview="sent",
        staleness_hours=0,
        message_count=2,
    )
    message = MessageSchema(
        id=message_id,
        thread_id=thread_id,
        graph_message_id="AAMkAG-msg-001",
        direction="outbound",
        sender="sales@example.com",
        body_text="Line A\nLine C",
        body_preview="Line A",
        received_at=now,
        to_recipients=["client@example.com"],
        cc_recipients=[],
    )
    draft = DraftResponseSchema.model_validate(
        {
            "id": draft_id,
            "thread_id": thread_id,
            "created_at": now,
            "subject_line": "Re: Hello",
            "reply_body": "Line A\nLine B",
            "teaching_note": "Note",
            "urgency": "HIGH",
            "urgency_reason": "Urgent",
            "suggested_recipients": [],
            "forward_to": None,
            "suggested_actions": [],
        }
    )
    sent = SentReplySchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        message_id=message_id,
        draft_id=draft_id,
        sent_body_snapshot="Line A\nLine C",
        sent_at=now,
        matched_by="approved_draft",
        created_at=now,
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.thread_view_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.thread_view_service.thread_repo.build_thread_summary",
            AsyncMock(return_value=summary),
        ),
        patch(
            "app.services.thread_view_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.thread_view_service.classification_repo.get_latest_for_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.draft_repo.get_latest_proposed_by_thread",
            AsyncMock(return_value=draft),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_by_thread_id",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_raw_by_conversation",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.get_latest_triage_flags",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=sent),
        ),
        patch(
            "app.services.thread_view_service.related_thread_service.list_stored_associations",
            AsyncMock(return_value=[]),
        ),
    ):
        detail = await thread_view_service.get_thread_detail(session, settings, thread_id)

    assert detail.sent_reply is not None
    assert detail.sent_reply.matched_by == "approved_draft"
    assert detail.draft_vs_sent_diff is not None
    assert "Line C" in detail.draft_vs_sent_diff.added
    assert "Line B" in detail.draft_vs_sent_diff.removed


@pytest.mark.asyncio
async def test_thread_view_omits_sent_reply_when_newer_inbound_is_tip() -> None:
    """Manual resolve after new mail must not resurrect an older Outlook send panel."""
    from app.repositories.message_repo import MessageSchema
    from app.repositories.sent_reply_repo import SentReplySchema
    from app.repositories.thread_repo import ThreadSchema

    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
        salute_directory_enabled=False,
    )
    thread_id = uuid.uuid4()
    outbound_id = uuid.uuid4()
    inbound_id = uuid.uuid4()
    sent_at = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
    inbound_at = datetime(2026, 9, 3, 11, 0, tzinfo=UTC)
    thread = ThreadSchema(
        id=thread_id,
        mailbox="sales@example.com",
        conversation_id="conv-followup",
        subject="Re: ticket",
        state="RESOLVED",
        urgency="NORMAL",
        category=None,
        last_message_at=inbound_at,
        last_updated_at=inbound_at,
    )
    summary = ThreadSummary(
        id=thread_id,
        mailbox="sales@example.com",
        mailbox_key="sales",
        subject="Re: ticket",
        state="RESOLVED",
        urgency="NORMAL",
        last_message_at=inbound_at,
        last_sender="client@example.com",
        preview="New question",
        staleness_hours=0,
        message_count=2,
    )
    outbound = MessageSchema(
        id=outbound_id,
        thread_id=thread_id,
        graph_message_id="AAMk-out",
        direction="outbound",
        sender="sales@example.com",
        body_text="We sent the packet yesterday.",
        body_preview="We sent the packet yesterday.",
        received_at=sent_at,
        to_recipients=["client@example.com"],
        cc_recipients=[],
    )
    inbound = MessageSchema(
        id=inbound_id,
        thread_id=thread_id,
        graph_message_id="AAMk-in",
        direction="inbound",
        sender="client@example.com",
        body_text="Thanks — one more question about billing.",
        body_preview="Thanks — one more question about billing.",
        received_at=inbound_at,
        to_recipients=["sales@example.com"],
        cc_recipients=[],
    )
    sent = SentReplySchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        message_id=outbound_id,
        draft_id=None,
        sent_body_snapshot="We sent the packet yesterday.",
        sent_at=sent_at,
        matched_by="time_window",
        created_at=sent_at,
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.thread_view_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.thread_view_service.thread_repo.build_thread_summary",
            AsyncMock(return_value=summary),
        ),
        patch(
            "app.services.thread_view_service.message_repo.list_by_thread",
            AsyncMock(return_value=[outbound, inbound]),
        ),
        patch(
            "app.services.thread_view_service.classification_repo.get_latest_for_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.draft_repo.get_latest_proposed_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_by_thread_id",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_raw_by_conversation",
            AsyncMock(
                return_value=[
                    {
                        "event_type": "thread.resolved.reviewer",
                        "created_at": inbound_at,
                        "payload": {
                            "human": {
                                "title": "Marked resolved",
                                "body": "You marked this thread resolved. Actions taken: Handled offline.",
                                "actor_kind": "elise",
                            }
                        },
                    }
                ]
            ),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.get_latest_triage_flags",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=sent),
        ),
        patch(
            "app.services.thread_view_service.related_thread_service.list_stored_associations",
            AsyncMock(return_value=[]),
        ),
    ):
        detail = await thread_view_service.get_thread_detail(session, settings, thread_id)

    assert detail.sent_reply is None
    assert detail.draft_vs_sent_diff is None
    assert detail.thread.presentation is not None
    assert detail.thread.presentation.resolution_mode == "manual"


@pytest.mark.asyncio
async def test_thread_view_heals_blank_sent_snapshot_from_linked_message() -> None:
    """Historical empty snapshots must surface the linked message unique reply."""
    from app.models.schemas.draft import DraftResponseSchema
    from app.repositories.message_repo import MessageSchema
    from app.repositories.sent_reply_repo import SentReplySchema
    from app.repositories.thread_repo import ThreadSchema

    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
        salute_directory_enabled=False,
    )
    thread_id = uuid.uuid4()
    message_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    now = datetime.now(UTC)
    thread = ThreadSchema(
        id=thread_id,
        mailbox="sales@example.com",
        conversation_id="conv-1",
        subject="Hello",
        state="RESOLVED",
        urgency="HIGH",
        category=None,
        last_message_at=now,
        last_updated_at=now,
    )
    summary = ThreadSummary(
        id=thread_id,
        mailbox="sales@example.com",
        mailbox_key="sales",
        subject="Hello",
        state="RESOLVED",
        urgency="HIGH",
        last_message_at=now,
        last_sender="sales@example.com",
        preview="sent",
        staleness_hours=0,
        message_count=2,
    )
    message = MessageSchema(
        id=message_id,
        thread_id=thread_id,
        graph_message_id="AAMkAG-msg-heal",
        direction="outbound",
        sender="sales@example.com",
        body_text="Shipped.\n\nFrom: Client <client@example.com>\nSent: Monday\n",
        body_preview="Shipped.",
        unique_body_text="Shipped.",
        received_at=now,
        to_recipients=["client@example.com"],
        cc_recipients=[],
    )
    draft = DraftResponseSchema.model_validate(
        {
            "id": draft_id,
            "thread_id": thread_id,
            "created_at": now,
            "subject_line": "Re: Hello",
            "reply_body": "We shipped it.",
            "teaching_note": "Note",
            "urgency": "HIGH",
            "urgency_reason": "Urgent",
            "suggested_recipients": [],
            "forward_to": None,
            "suggested_actions": [],
        }
    )
    sent = SentReplySchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        message_id=message_id,
        draft_id=draft_id,
        sent_body_snapshot="",
        sent_at=now,
        matched_by="approved_draft",
        created_at=now,
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.thread_view_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.thread_view_service.thread_repo.build_thread_summary",
            AsyncMock(return_value=summary),
        ),
        patch(
            "app.services.thread_view_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.thread_view_service.classification_repo.get_latest_for_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.draft_repo.get_latest_proposed_by_thread",
            AsyncMock(return_value=draft),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_by_thread_id",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_raw_by_conversation",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.get_latest_triage_flags",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=sent),
        ),
        patch(
            "app.services.thread_view_service.related_thread_service.list_stored_associations",
            AsyncMock(return_value=[]),
        ),
    ):
        detail = await thread_view_service.get_thread_detail(session, settings, thread_id)

    assert detail.sent_reply is not None
    assert detail.sent_reply.sent_body_snapshot == "Shipped."
    assert detail.draft_vs_sent_diff is not None
    assert "Shipped." in detail.draft_vs_sent_diff.added
    assert "We shipped it." in detail.draft_vs_sent_diff.removed


@pytest.mark.asyncio
async def test_thread_view_omits_sent_reply_panel_for_meeting_accept() -> None:
    """Meeting accepts must not surface the learning / draft-vs-sent panel."""
    from app.repositories.message_repo import MessageSchema
    from app.repositories.sent_reply_repo import SentReplySchema
    from app.repositories.thread_repo import ThreadSchema

    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
        salute_directory_enabled=False,
    )
    thread_id = uuid.uuid4()
    message_id = uuid.uuid4()
    now = datetime.now(UTC)
    thread = ThreadSchema(
        id=thread_id,
        mailbox="sales@example.com",
        conversation_id="conv-meet",
        subject="Accepted: Sync",
        state="RESOLVED",
        urgency=None,
        category=None,
        last_message_at=now,
        last_updated_at=now,
    )
    summary = ThreadSummary(
        id=thread_id,
        mailbox="sales@example.com",
        mailbox_key="sales",
        subject="Accepted: Sync",
        state="RESOLVED",
        urgency=None,
        last_message_at=now,
        last_sender="client@example.com",
        preview="",
        staleness_hours=0,
        message_count=1,
    )
    message = MessageSchema(
        id=message_id,
        thread_id=thread_id,
        graph_message_id="AAMkAG-meet-accept",
        direction="outbound",
        sender="sales@example.com",
        body_text="",
        body_preview="Accepted: Sync",
        unique_body_text="",
        received_at=now,
        meeting_message_type="meetingAccepted",
    )
    sent = SentReplySchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        message_id=message_id,
        draft_id=None,
        sent_body_snapshot="",
        sent_at=now,
        matched_by="time_window",
        created_at=now,
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.thread_view_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.thread_view_service.thread_repo.build_thread_summary",
            AsyncMock(return_value=summary),
        ),
        patch(
            "app.services.thread_view_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.thread_view_service.classification_repo.get_latest_for_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.draft_repo.get_latest_proposed_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_by_thread_id",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_raw_by_conversation",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.get_latest_triage_flags",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=sent),
        ),
        patch(
            "app.services.thread_view_service.related_thread_service.list_stored_associations",
            AsyncMock(return_value=[]),
        ),
    ):
        detail = await thread_view_service.get_thread_detail(session, settings, thread_id)

    assert detail.sent_reply is None
    assert detail.draft_vs_sent_diff is None


@pytest.mark.asyncio
async def test_thread_view_omits_sent_reply_panel_when_snapshot_still_empty() -> None:
    """Empty Outlook send with no recoverable body must not claim learning."""
    from app.repositories.message_repo import MessageSchema
    from app.repositories.sent_reply_repo import SentReplySchema
    from app.repositories.thread_repo import ThreadSchema

    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
        salute_directory_enabled=False,
    )
    thread_id = uuid.uuid4()
    message_id = uuid.uuid4()
    now = datetime.now(UTC)
    thread = ThreadSchema(
        id=thread_id,
        mailbox="sales@example.com",
        conversation_id="conv-empty",
        subject="Re: Hello",
        state="RESOLVED",
        urgency=None,
        category=None,
        last_message_at=now,
        last_updated_at=now,
    )
    summary = ThreadSummary(
        id=thread_id,
        mailbox="sales@example.com",
        mailbox_key="sales",
        subject="Re: Hello",
        state="RESOLVED",
        urgency=None,
        last_message_at=now,
        last_sender="sales@example.com",
        preview="",
        staleness_hours=0,
        message_count=1,
    )
    message = MessageSchema(
        id=message_id,
        thread_id=thread_id,
        graph_message_id="AAMkAG-empty",
        direction="outbound",
        sender="sales@example.com",
        body_text="",
        body_preview="",
        unique_body_text="",
        received_at=now,
    )
    sent = SentReplySchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        message_id=message_id,
        draft_id=None,
        sent_body_snapshot="",
        sent_at=now,
        matched_by="time_window",
        created_at=now,
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.thread_view_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.thread_view_service.thread_repo.build_thread_summary",
            AsyncMock(return_value=summary),
        ),
        patch(
            "app.services.thread_view_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.thread_view_service.classification_repo.get_latest_for_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.draft_repo.get_latest_proposed_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_by_thread_id",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_raw_by_conversation",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.get_latest_triage_flags",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=sent),
        ),
        patch(
            "app.services.thread_view_service.related_thread_service.list_stored_associations",
            AsyncMock(return_value=[]),
        ),
    ):
        detail = await thread_view_service.get_thread_detail(session, settings, thread_id)

    assert detail.sent_reply is None


@pytest.mark.asyncio
async def test_thread_view_proposed_skips_outlook_catchup_latest() -> None:
    """Latest catch-up draft must not replace the LLM proposed body in the UI."""
    from app.models.schemas.draft import DraftResponseSchema
    from app.repositories.message_repo import MessageSchema
    from app.repositories.sent_reply_repo import SentReplySchema
    from app.repositories.thread_repo import ThreadSchema

    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
        salute_directory_enabled=False,
    )
    thread_id = uuid.uuid4()
    message_id = uuid.uuid4()
    llm_draft_id = uuid.uuid4()
    now = datetime.now(UTC)
    llm_teaching = (
        "Vendor clarified ElectronicClientID; reply should restate the multi-tenant goal."
    )
    thread = ThreadSchema(
        id=thread_id,
        mailbox="sales@example.com",
        conversation_id="conv-1",
        subject="Hello",
        state="RESOLVED",
        urgency="NORMAL",
        category=None,
        last_message_at=now,
        last_updated_at=now,
    )
    summary = ThreadSummary(
        id=thread_id,
        mailbox="sales@example.com",
        mailbox_key="sales",
        subject="Hello",
        state="RESOLVED",
        urgency="NORMAL",
        last_message_at=now,
        last_sender="sales@example.com",
        preview="sent",
        staleness_hours=0,
        message_count=2,
    )
    message = MessageSchema(
        id=message_id,
        thread_id=thread_id,
        graph_message_id="AAMkAG-msg-sent",
        direction="outbound",
        sender="sales@example.com",
        body_text="Thanks for the follow-up Beau. This helps us adjust.",
        body_preview="Thanks for the follow-up",
        received_at=now,
        to_recipients=["client@example.com"],
        cc_recipients=[],
    )
    llm_draft = DraftResponseSchema.model_validate(
        {
            "id": llm_draft_id,
            "thread_id": thread_id,
            "created_at": now,
            "subject_line": "Re: Hello",
            "reply_body": "Hi Beau,\n\nThank you for checking on that.",
            "teaching_note": llm_teaching,
            "urgency": "NORMAL",
            "urgency_reason": "Clarifying question",
            "suggested_recipients": [],
            "forward_to": None,
            "suggested_actions": [],
        }
    )
    sent = SentReplySchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        message_id=message_id,
        draft_id=llm_draft_id,
        sent_body_snapshot="Thanks for the follow-up Beau. This helps us adjust.",
        sent_at=now,
        matched_by="time_window",
        created_at=now,
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.thread_view_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.thread_view_service.thread_repo.build_thread_summary",
            AsyncMock(return_value=summary),
        ),
        patch(
            "app.services.thread_view_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.thread_view_service.classification_repo.get_latest_for_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.draft_repo.get_latest_proposed_by_thread",
            AsyncMock(return_value=llm_draft),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_by_thread_id",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_raw_by_conversation",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.get_latest_triage_flags",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=sent),
        ),
        patch(
            "app.services.thread_view_service.related_thread_service.list_stored_associations",
            AsyncMock(return_value=[]),
        ),
    ):
        detail = await thread_view_service.get_thread_detail(session, settings, thread_id)

    assert detail.draft is not None
    assert detail.draft.body == "Hi Beau,\n\nThank you for checking on that."
    assert detail.draft.teaching_note == llm_teaching
    assert detail.draft_vs_sent_diff is not None
    assert "Thanks for the follow-up Beau. This helps us adjust." in detail.draft_vs_sent_diff.added
    assert "Hi Beau," in detail.draft_vs_sent_diff.removed or (
        "Thank you for checking on that." in detail.draft_vs_sent_diff.removed
    )


def test_compute_draft_vs_sent_diff_ignores_quoted_history() -> None:
    proposed = "Thanks for reaching out.\nWe can help."
    sent = (
        "Thanks for reaching out.\nWe can help.\n"
        "\nFrom: Client <client@example.com>\n"
        "Sent: Monday, August 11, 2026 9:00 AM\n"
        "To: info@example.com\n"
        "Subject: Help\n"
        "\nOriginal question with https://orders.example.com/very/long/path"
    )
    diff = thread_view_service.compute_draft_vs_sent_diff(proposed, sent)
    assert diff is not None
    assert diff.added == []
    assert diff.removed == []


def test_compute_draft_vs_sent_diff_still_flags_reply_edits() -> None:
    proposed = "Please call us.\nThanks"
    sent = "Please email us.\nThanks\n\n-----Original Message-----\nFrom: Client\n"
    diff = thread_view_service.compute_draft_vs_sent_diff(proposed, sent)
    assert diff is not None
    assert "Please email us." in diff.added
    assert "Please call us." in diff.removed


@pytest.mark.asyncio
async def test_list_thread_audit_requires_mailbox_scope() -> None:
    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
        salute_directory_enabled=False,
    )
    thread_id = uuid.uuid4()
    now = datetime.now(UTC)
    thread = SimpleNamespace(
        id=thread_id,
        mailbox="sales@example.com",
        conversation_id="conv-audit-1",
        subject="Hello",
        state="AWAITING_ACTION",
        urgency="NORMAL",
        category=None,
        last_message_at=now,
        last_updated_at=now,
    )
    audit_mock = AsyncMock(return_value=[])
    session = AsyncMock()
    with (
        patch(
            "app.services.thread_view_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_by_thread_id",
            audit_mock,
        ),
    ):
        await thread_view_service.list_thread_audit(session, settings, thread_id)

    audit_mock.assert_awaited_once_with(
        session,
        thread_id,
        "conv-audit-1",
        mailbox="sales@example.com",
    )
