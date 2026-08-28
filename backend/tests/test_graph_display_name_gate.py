"""Graph From display names are gated before they become a salute."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.reply_addressee import gated_graph_display_first_name, resolve_reply_addressee
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema


def test_person_display_name_yields_first_name() -> None:
    assert (
        gated_graph_display_first_name(
            "Abu Bakkar Siddiq",
            "siddiq@sample-partner.example.com",
        )
        == "Abu"
    )


def test_google_calendar_system_label_is_rejected() -> None:
    assert (
        gated_graph_display_first_name(
            "Google Calendar",
            "calendar-notification@google.com",
        )
        is None
    )


def test_phmsa_subscriptions_brand_is_rejected() -> None:
    assert (
        gated_graph_display_first_name(
            "PHMSA Subscriptions",
            "phmsa.subscriptions@info.dot.gov",
        )
        is None
    )


def test_local_part_display_is_rejected() -> None:
    assert gated_graph_display_first_name("Samplecontact", "samplecontact@sample-information.example.com") is None


def test_resolve_uses_gated_graph_name_not_local_part() -> None:
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
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        suppress_local_part=True,
    )
    assert addressee is not None
    assert addressee.salute_name == "Abu"
    assert addressee.source_kind == "display"


def test_resolve_empty_outbound_accept_still_uses_inbound_graph_name() -> None:
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
        suppress_local_part=True,
    )
    assert addressee is not None
    assert addressee.email == "siddiq@sample-partner.example.com"
    assert addressee.salute_name == "Abu"
    assert addressee.source_kind == "display"
