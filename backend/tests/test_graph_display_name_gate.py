"""Graph From display names are not a salute — contact or signature only."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.reply_addressee import resolve_reply_addressee
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema


def test_resolve_does_not_salute_graph_display_name() -> None:
    """Calendar From 'Abu Bakkar Siddiq' is not a signature. No contact → Hi,."""
    mailbox = "sampleagent@sample-site.example.com"
    messages = [
        EmailMessageSchema(
            message_id="1",
            conversation_id="conv-1",
            mailbox=mailbox,
            sender="siddiq@sample-partner.example.com",
            sender_display_name="Abu Bakkar Siddiq",
            subject="Invitation: IDME's Demo - 2nd Week",
            body_text="Join with Google Meet",
            received_at=datetime(2026, 8, 28, 6, 27, tzinfo=UTC),
            direction=EmailDirectionEnum.INBOUND,
            to_recipients=[mailbox],
        )
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == ""
    assert addressee.source_kind == "none"


def test_resolve_does_not_salute_graph_name_on_empty_outbound_accept() -> None:
    mailbox = "sampleagent@sample-site.example.com"
    messages = [
        EmailMessageSchema(
            message_id="1",
            conversation_id="conv-1",
            mailbox=mailbox,
            sender="siddiq@sample-partner.example.com",
            sender_display_name="Abu Bakkar Siddiq",
            subject="Invitation: IDME's Demo - 2nd Week",
            body_text="Join with Google Meet",
            received_at=datetime(2026, 8, 28, 6, 27, tzinfo=UTC),
            direction=EmailDirectionEnum.INBOUND,
            to_recipients=[mailbox],
        ),
        EmailMessageSchema(
            message_id="2",
            conversation_id="conv-1",
            mailbox=mailbox,
            sender=mailbox,
            subject="Accepted: IDME's Demo - 2nd Week",
            body_text="",
            received_at=datetime(2026, 8, 28, 14, 56, tzinfo=UTC),
            direction=EmailDirectionEnum.OUTBOUND,
            to_recipients=["siddiq@sample-partner.example.com"],
            meeting_message_type="meetingAccepted",
        ),
    ]
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        mailbox_owner="Elise",
    )
    assert addressee is not None
    assert addressee.email == "siddiq@sample-partner.example.com"
    assert addressee.salute_name == ""
