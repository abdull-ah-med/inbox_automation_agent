"""Reply addressee: who the draft should salute / primarily address."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.reply_addressee import (
    resolve_reply_addressee,
    salute_name_from_party,
)
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema


def _msg(
    *,
    message_id: str,
    sender: str,
    direction: EmailDirectionEnum,
    to: list[str] | None = None,
    mailbox: str = "inquiries@sample-site.example.com",
) -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id=message_id,
        conversation_id="conv-1",
        mailbox=mailbox,
        sender=sender,
        subject="Hello",
        body_text="Body",
        body_preview="Body",
        received_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        direction=direction,
        to_recipients=list(to or []),
    )


def test_salute_name_uses_display_first_name() -> None:
    assert salute_name_from_party("Smit Patel <smit.patel@sample-transport.example.com>") == "Smit"
    assert salute_name_from_party("Divyansh <Dev@sample-site.example.com>") == "Divyansh"


def test_salute_name_falls_back_to_local_part() -> None:
    assert salute_name_from_party("Dev@sample-site.example.com") == "Dev"
    assert salute_name_from_party("smit.patel@sample-transport.example.com") == "Smit"


def test_latest_outbound_to_wins_over_earlier_inbound_sender() -> None:
    """Multi-party: opener is Smit; tip of thread is outbound to Dev → salute Dev."""
    mailbox = "inquiries@sample-site.example.com"
    messages = [
        _msg(
            message_id="1",
            sender="Smit Patel <smit.patel@sample-transport.example.com>",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
        ),
        _msg(
            message_id="2",
            sender=mailbox,
            direction=EmailDirectionEnum.OUTBOUND,
            to=["Dev@sample-site.example.com"],
            mailbox=mailbox,
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.email == "dev@sample-site.example.com"
    assert addressee.salute_name == "Dev"
    assert addressee.source == "last_outbound_to"


def test_latest_inbound_wins_over_earlier_different_inbound() -> None:
    mailbox = "inquiries@sample-site.example.com"
    messages = [
        _msg(
            message_id="1",
            sender="Smit Patel <smit.patel@sample-transport.example.com>",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
        ),
        _msg(
            message_id="2",
            sender="Divyansh <Dev@sample-site.example.com>",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.email == "dev@sample-site.example.com"
    assert addressee.salute_name == "Divyansh"
    assert addressee.source == "latest_inbound"


def test_single_inbound_uses_that_sender() -> None:
    mailbox = "sales@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="vendor@example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.email == "vendor@example.com"
    assert addressee.source == "latest_inbound"
