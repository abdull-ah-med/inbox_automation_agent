"""Gated Graph From display names — calendar/noreply blocked; person-shaped allowed."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.reply_addressee import resolve_reply_addressee
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema


def test_resolve_does_not_salute_calendar_display_name() -> None:
    """Calendar meetingRequest From stays empty even with a person-shaped display."""
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
            meeting_message_type="meetingRequest",
        )
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == ""
    assert addressee.source_kind == "none"


def test_resolve_salutes_person_shaped_display_on_normal_inbound() -> None:
    mailbox = "elise@example.com"
    messages = [
        EmailMessageSchema(
            message_id="1",
            conversation_id="conv-1",
            mailbox=mailbox,
            sender="kelvin@client.com",
            sender_display_name="Kelvin Collado",
            subject="Screens updated",
            body_text="Both invoice screens are updated.",
            received_at=datetime(2026, 8, 28, 6, 27, tzinfo=UTC),
            direction=EmailDirectionEnum.INBOUND,
            to_recipients=[mailbox],
        )
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == "Kelvin"
    assert addressee.source_kind == "display"


def test_resolve_rejects_org_shaped_display_name() -> None:
    mailbox = "elise@example.com"
    messages = [
        EmailMessageSchema(
            message_id="1",
            conversation_id="conv-1",
            mailbox=mailbox,
            sender="newsletter@vendor.example",
            sender_display_name="Vendor Newsletter",
            subject="Monthly update",
            body_text="Registration is open.",
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
            meeting_message_type="meetingRequest",
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


def test_directory_overrides_display_name() -> None:
    mailbox = "elise@example.com"
    messages = [
        EmailMessageSchema(
            message_id="1",
            conversation_id="conv-1",
            mailbox=mailbox,
            sender="kelvin@client.com",
            sender_display_name="Kelvin Collado",
            subject="Update",
            body_text="Done.",
            received_at=datetime(2026, 8, 28, 6, 27, tzinfo=UTC),
            direction=EmailDirectionEnum.INBOUND,
            to_recipients=[mailbox],
        )
    ]
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        directory={"kelvin@client.com": "Kel"},
    )
    assert addressee is not None
    assert addressee.salute_name == "Kel"
    assert addressee.directory_hit is True
    assert addressee.source_kind == "directory"
