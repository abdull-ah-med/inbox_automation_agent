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
    settings = Settings(environment="local", target_mailboxes="sales@example.com")
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

    settings = Settings(environment="local", target_mailboxes="sales@example.com")
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
            "app.services.thread_view_service.draft_repo.get_latest_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_by_thread_id",
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

    settings = Settings(environment="local", target_mailboxes="sales@example.com")
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
            "app.services.thread_view_service.draft_repo.get_latest_by_thread",
            AsyncMock(return_value=draft),
        ),
        patch(
            "app.services.thread_view_service.audit_repo.list_by_thread_id",
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
    sent = (
        "Please email us.\nThanks\n"
        "\n-----Original Message-----\n"
        "From: Client\n"
    )
    diff = thread_view_service.compute_draft_vs_sent_diff(proposed, sent)
    assert diff is not None
    assert "Please email us." in diff.added
    assert "Please call us." in diff.removed


@pytest.mark.asyncio
async def test_list_thread_audit_requires_mailbox_scope() -> None:
    settings = Settings(environment="local", target_mailboxes="sales@example.com")
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
