"""Directory override + empty-sentinel salute for resolve_reply_addressee."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

from app.core.reply_addressee import (
    format_reply_addressee_block,
    resolve_reply_addressee,
)
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema


def _msg(
    *,
    message_id: str,
    sender: str,
    direction: EmailDirectionEnum,
    to: list[str] | None = None,
    mailbox: str = "elise@example.com",
    body_text: str = "Body",
) -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id=message_id,
        conversation_id="conv-1",
        mailbox=mailbox,
        sender=sender,
        subject="Hello",
        body_text=body_text,
        body_preview=body_text[:80],
        received_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        direction=direction,
        to_recipients=list(to or []),
    )


def test_resolve_reply_addressee_stays_synchronous() -> None:
    assert not inspect.iscoroutinefunction(resolve_reply_addressee)


def test_directory_overrides_display_name() -> None:
    mailbox = "elise@example.com"
    messages = [
        _msg(
            message_id="1",
            sender='"Kelvin C." <samplecontact@sample-vendor.example.com>',
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
        ),
    ]
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        directory={"samplecontact@sample-vendor.example.com": "Kelvin"},
    )
    assert addressee is not None
    assert addressee.salute_name == "Kelvin"
    assert addressee.directory_hit is True
    assert addressee.source_kind == "directory"


def test_directory_overrides_signature() -> None:
    mailbox = "elise@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="samplecontact@sample-vendor.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.\n\nThanks,\nKel\n",
        ),
    ]
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        directory={"samplecontact@sample-vendor.example.com": "Kelvin"},
    )
    assert addressee is not None
    assert addressee.salute_name == "Kelvin"
    assert addressee.directory_hit is True
    assert addressee.source_kind == "directory"


def test_bare_email_without_directory_uses_empty_sentinel() -> None:
    """samplecontact@ with no display/signature/directory must not leak 'Samplecontact'."""
    mailbox = "elise@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="samplecontact@sample-vendor.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == ""
    assert addressee.directory_hit is False
    assert addressee.source_kind == "none"


def test_signature_sourced_salute_not_emptied_even_if_equals_local_part() -> None:
    """Signature 'Samplecontact' that matches local-part stays (source_kind=signature)."""
    mailbox = "elise@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="samplecontact@sample-vendor.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.\n\nThanks,\nSamplecontact\n",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == "Samplecontact"
    assert addressee.source_kind == "signature"
    assert addressee.directory_hit is False


def test_directory_overrides_role_mailbox() -> None:
    """Contacts win first — a taught alias for Dev@ should not stay 'team'."""
    mailbox = "elise@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="dev@sample-site.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.",
        ),
    ]
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        directory={"dev@sample-site.example.com": "Divyansh"},
    )
    assert addressee is not None
    assert addressee.salute_name == "Divyansh"
    assert addressee.directory_hit is True
    assert addressee.source_kind == "directory"


def test_role_mailbox_without_directory_uses_bare_hi() -> None:
    mailbox = "elise@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="dev@sample-site.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == ""
    assert addressee.source_kind == "none"


def test_priority_contacts_before_signature() -> None:
    """Contacts → signature person name → bare Hi,. Never locals or roles."""
    mailbox = "elise@example.com"
    messages = [
        _msg(
            message_id="1",
            sender='"Someone Else" <samplecontact@sample-vendor.example.com>',
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.\n\nThanks,\nKel\n",
        ),
    ]
    with_contact = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        directory={"samplecontact@sample-vendor.example.com": "Kelvin"},
    )
    without = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert with_contact is not None and without is not None
    assert with_contact.salute_name == "Kelvin"
    assert with_contact.source_kind == "directory"
    # No contact → signature "Kel" beats display "Someone"
    assert without.salute_name == "Kel"
    assert without.source_kind == "signature"


def test_mailbox_owner_not_saluted() -> None:
    mailbox = "info@sample-services.example.com"
    messages = [
        _msg(
            message_id="1",
            sender="Elise <elise@example.com>",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.",
        ),
    ]
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        mailbox_owner="Elise",
    )
    assert addressee is not None
    assert addressee.salute_name != "Elise"
    assert addressee.salute_name.casefold() != "elise"


def test_directory_case_insensitive_lookup() -> None:
    mailbox = "elise@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="k@example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.",
        ),
    ]
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        directory={"K@Example.com": "Kelvin"},
    )
    assert addressee is not None
    assert addressee.salute_name == "Kelvin"
    assert addressee.directory_hit is True
    assert addressee.source_kind == "directory"


def test_format_block_empty_salute_requires_bare_hi() -> None:
    from app.core.reply_addressee import ReplyAddressee

    block = format_reply_addressee_block(
        ReplyAddressee(
            raw="samplecontact@sample-vendor.example.com",
            email="samplecontact@sample-vendor.example.com",
            salute_name="",
            source="latest_inbound",
            directory_hit=False,
            source_kind="none",
        )
    )
    assert "Salute: (none — no personal name known)" in block
    assert '"Hi,"' in block or "Hi," in block
    assert "do NOT" in block or "do not" in block.lower()
    assert "local-part" in block.lower() or "email address" in block.lower()
