"""Draft prompt includes directory salute, not bare local-part."""

from __future__ import annotations

from datetime import UTC, datetime

from app.llm import draft_generator as draft_llm
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema


def _triage() -> TriageResultSchema:
    return TriageResultSchema(
        is_spam=False,
        spam_reason=None,
        has_action_items=True,
        action_items_summary="Follow up",
        needs_context=False,
        context_reason=None,
    )


def test_build_user_content_uses_directory_salute_not_local_part() -> None:
    mailbox = "sales@example.com"
    tip = EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox=mailbox,
        sender="samplecontact@sample-vendor.example.com",
        subject="Quote request",
        body_text="Can you send pricing?",
        body_preview="Can you send",
        received_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=[mailbox],
        cc_recipients=[],
    )
    context = ThreadContextSchema(
        conversation_id=tip.conversation_id,
        mailbox=mailbox,
        subject=tip.subject,
        messages=[tip],
    )
    content = draft_llm._build_user_content(
        tip,
        context,
        _triage(),
        directory={"samplecontact@sample-vendor.example.com": "Kelvin"},
        suppress_local_part=True,
    )
    assert "Salute: Kelvin" in content
    assert "Salute: Samplecontact" not in content
