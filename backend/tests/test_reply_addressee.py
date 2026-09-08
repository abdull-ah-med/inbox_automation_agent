"""Reply addressee: who the draft should salute / primarily address."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.reply_addressee import (
    participant_first_names,
    resolve_reply_addressee,
)
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema


def _msg(
    *,
    message_id: str,
    sender: str,
    direction: EmailDirectionEnum,
    to: list[str] | None = None,
    mailbox: str = "inquiries@sample-site.example.com",
    body_text: str = "Body",
    sender_display_name: str | None = None,
) -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id=message_id,
        conversation_id="conv-1",
        mailbox=mailbox,
        sender=sender,
        sender_display_name=sender_display_name,
        subject="Hello",
        body_text=body_text,
        body_preview=body_text[:80],
        received_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        direction=direction,
        to_recipients=list(to or []),
    )


def test_role_mailbox_graph_title_display_is_not_saluted() -> None:
    """Dev@ Graph From 'Sample Developer' is a role label — same as no name."""
    mailbox = "sampleagent@sample-site.example.com"
    messages = [
        _msg(
            message_id="1",
            sender="Dev@sample-site.example.com",
            sender_display_name="Sample Developer",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="When I checked that sent email activity the button works.",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == ""
    assert addressee.source_kind == "none"


def test_participant_names_follow_salute_priority() -> None:
    """Timeline/facts use the same names drafts salute — never local-part or role labels."""
    mailbox = "sampleagent@sample-site.example.com"
    messages = [
        _msg(
            message_id="1",
            sender="Dev@sample-site.example.com",
            sender_display_name="Sample Developer",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="When I checked that sent email activity the button works.",
        ),
        _msg(
            message_id="2",
            sender=mailbox,
            sender_display_name="Elise Chouest",
            direction=EmailDirectionEnum.OUTBOUND,
            to=["Dev@sample-site.example.com"],
            mailbox=mailbox,
            body_text="Hey Div,\nLooks like the link is broken.\nThanks,\nElise\n",
        ),
    ]
    names = participant_first_names(
        mailbox=mailbox,
        messages=messages,
        mailbox_owner="Elise",
    )
    assert names.get("sampleagent@sample-site.example.com") == "Elise"
    assert "dev@sample-site.example.com" not in names
    assert "Sample" not in names.values()
    assert "Dev" not in names.values()

    signed = [
        _msg(
            message_id="1",
            sender="Dev@sample-site.example.com",
            sender_display_name="Sample Developer",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Screens are updated.\n\nThanks,\nDivyansh\n",
        ),
    ]
    signed_names = participant_first_names(mailbox=mailbox, messages=signed)
    assert signed_names["dev@sample-site.example.com"] == "Divyansh"

    taught = participant_first_names(
        mailbox=mailbox,
        messages=messages,
        mailbox_owner="Elise",
        directory={"dev@sample-site.example.com": "Divyansh"},
    )
    assert taught["dev@sample-site.example.com"] == "Divyansh"
    assert taught["sampleagent@sample-site.example.com"] == "Elise"


def test_role_mailbox_inbound_uses_signature_name_not_local_part() -> None:
    """Dev@ is a role mailbox; signature Divyansh is the person to salute."""
    mailbox = "sampleagent@sample-site.example.com"
    body = "Hi Elise,\n\nI have updated both invoice screens.\n\nThanks,\nDivyansh\n"
    messages = [
        _msg(
            message_id="1",
            sender="Dev@sample-site.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text=body,
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.email == "dev@sample-site.example.com"
    assert addressee.salute_name == "Divyansh"


def test_zendesk_helpdesk_salutes_agent_not_customer_or_quoted_owner() -> None:
    """Zendesk tip greets our owner (Elise) and embeds her prior Thanks,Elise.

    Reply must salute the agent (Alex), never the mailbox owner we sign as.
    """
    mailbox = "info@sample-services.example.com"
    tip = (
        "##- Please type your reply above this line -##\n\n"
        "Your request (40197) has been updated. To add additional comments, "
        "reply to this email.\n\n"
        "Alex Taylor (SampleHelpdesk)\n\n"
        "Aug 25, 2026, 8:55 AM MDT\n\n"
        "Elise,\n\n"
        "I am happy to look into this for you. Can you provide a search ID?\n\n"
        "Alex Taylor\n"
        "Customer Support\n\n"
        "[https://sample-helpdesk.example.com/images/2016/default-avatar-80.png]\n\n"
        "info\n\n"
        "Aug 25, 2026, 8:40 AM MDT\n\n"
        "Hello,\n\n"
        "Please check notifications.\n\n"
        "Thanks,\n"
        "Elise\n"
    )
    messages = [
        _msg(
            message_id="1",
            sender="helpdesk@sample-helpdesk.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text=tip,
        ),
    ]
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        mailbox_owner="Elise",
    )
    assert addressee is not None
    assert addressee.email == "helpdesk@sample-helpdesk.example.com"
    assert addressee.salute_name == "Alex"


def test_role_mailbox_outbound_tip_uses_prior_inbound_display_or_signature() -> None:
    """Tip is outbound to Dev@; earlier inbound from same address signed Divyansh."""
    mailbox = "sampleagent@sample-site.example.com"
    messages = [
        _msg(
            message_id="1",
            sender="Dev@sample-site.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Layout is done.\n\nThanks,\nDivyansh\n",
        ),
        _msg(
            message_id="2",
            sender=mailbox,
            direction=EmailDirectionEnum.OUTBOUND,
            to=["Dev@sample-site.example.com"],
            mailbox=mailbox,
            body_text="Thanks Div.",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.email == "dev@sample-site.example.com"
    assert addressee.salute_name == "Divyansh"


def test_person_local_part_without_signature_uses_empty_sentinel() -> None:
    """Bare jane.doe@ with no display/signature must not salute 'Jane' from local-part."""
    mailbox = "sales@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="jane.doe@vendor.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.\n\nJane Doe\nVendor Co\n",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == ""
    assert addressee.source_kind == "none"
    assert addressee.directory_hit is False


def test_person_local_part_with_signature_uses_first_name() -> None:
    mailbox = "sales@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="jane.doe@vendor.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.\n\nThanks,\nJane\n",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == "Jane"
    assert addressee.source_kind == "signature"


def test_best_closing_signature_uses_first_name() -> None:
    mailbox = "sales@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="jane@vendor.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.\n\nBest,\nJane\n",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == "Jane"
    assert addressee.source_kind == "signature"


def test_talon_dash_signature_uses_first_name() -> None:
    mailbox = "sales@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="jane@vendor.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Please advise.\n\n--\nJane Smith\n",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == "Jane"
    assert addressee.source_kind == "talon_signature"


def test_mobile_stub_alone_does_not_salute() -> None:
    mailbox = "sales@example.com"
    messages = [
        _msg(
            message_id="1",
            sender="jane@vendor.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Thanks.\n\nSent from my iPhone\n",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == ""


def test_surname_local_part_prefers_signature_first_name() -> None:
    """Worked example: cblumenthal@… signs as Cydney Blumenthal / Director.

    Local-part fallback would salute "Cblumenthal"; the draft must use Cydney.
    """
    mailbox = "info@sample-services.example.com"
    body = (
        "Hi!\n\n"
        "Below is an email chain between applicant Davien Neal and our client. "
        "Is there anything else that needs to be done?\n\n"
        "Cydney Blumenthal\n\n"
        "Director of Digital Services\n\n"
        "Office 202-555-0103\n"
        "Mobile 202-555-0104\n"
        "Email cblumenthal@sample-vendor.example.com\n"
    )
    messages = [
        _msg(
            message_id="1",
            sender="cblumenthal@sample-vendor.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text=body,
        ),
    ]
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        mailbox_owner="Elise",
    )
    assert addressee is not None
    assert addressee.email == "cblumenthal@sample-vendor.example.com"
    assert addressee.salute_name == "Cydney"


def test_zendesk_auto_ack_without_agent_uses_bare_hi() -> None:
    """Zendesk 'request received' has no agent byline — only quoted prior mail.

    Saluting Helpdesk, Elise, or 'team' is wrong. No contact and no person
    sign → bare Hi,.
    """
    mailbox = "info@sample-services.example.com"
    tip = (
        "##- Please type your reply above this line -##\n\n"
        "Your request (40214) has been received and is being reviewed by our "
        "support staff.\n\n"
        "To add additional comments, reply to this email.\n\n"
        "[https://sample-helpdesk.example.com/images/2016/default-avatar-80.png]\n\n"
        "info\n\n"
        "Aug 26, 2026, 7:22 AM MDT\n\n"
        "INTERNAL: This message originated inside the organization.\n"
        "Hi team,\n\n"
        "We've had a few candidates report certificate warnings.\n\n"
        "Best,\n"
        "Elise\n"
    )
    messages = [
        _msg(
            message_id="1",
            sender="helpdesk@sample-helpdesk.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text=tip,
        ),
    ]
    addressee = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        mailbox_owner="Elise",
    )
    assert addressee is not None
    assert addressee.email == "helpdesk@sample-helpdesk.example.com"
    assert addressee.salute_name == ""


def test_zendesk_auto_ack_never_salutes_owner_even_when_quote_strip_leaks() -> None:
    """If prior Thanks,Elise leaks into the unique tip, still do not salute Elise."""
    mailbox = "info@sample-services.example.com"
    tip = (
        "##- Please type your reply above this line -##\n\n"
        "Your request (40214) has been updated. To add additional comments, "
        "reply to this email.\n\n"
        "Thanks,\n"
        "Elise\n"
    )
    messages = [
        _msg(
            message_id="1",
            sender="helpdesk@sample-helpdesk.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text=tip,
        ),
    ]
    with_owner = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        mailbox_owner="Elise",
    )
    without_owner = resolve_reply_addressee(
        mailbox=mailbox,
        messages=messages,
        mailbox_owner=None,
    )
    assert with_owner is not None
    assert with_owner.salute_name == ""
    assert with_owner.salute_name.casefold() != "elise"
    # Must not depend on MAILBOX_OWNERS being configured.
    assert without_owner is not None
    assert without_owner.salute_name == ""
    assert without_owner.salute_name.casefold() != "elise"


def test_latest_outbound_to_wins_over_earlier_inbound_sender() -> None:
    """Multi-party: opener is Smit; tip is outbound to Dev@ with no person sign.

    Primary To stays Dev@; salute is empty (never a role label or local-part).
    """
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
    assert addressee.salute_name == ""
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
            body_text="Please send the packet.\n\nThanks,\nSmit\n",
        ),
        _msg(
            message_id="2",
            sender="Divyansh <Dev@sample-site.example.com>",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Updated the layout.\n\nThanks,\nDivyansh\n",
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
    assert addressee.salute_name == ""


def test_company_brand_sign_off_is_not_a_person_salute() -> None:
    """'Thanks, SampleHelpdesk' is a brand, not Hi SampleHelpdesk."""
    mailbox = "info@sample-services.example.com"
    messages = [
        _msg(
            message_id="1",
            sender="helpdesk@sample-helpdesk.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Your ticket was updated.\n\nThanks,\nSampleHelpdesk\n",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == ""


def test_two_word_company_sign_off_is_not_a_person_salute() -> None:
    """'Thanks, Sample Helpdesk' must not become Hi Digital,."""
    mailbox = "info@sample-services.example.com"
    messages = [
        _msg(
            message_id="1",
            sender="helpdesk@sample-helpdesk.example.com",
            direction=EmailDirectionEnum.INBOUND,
            to=[mailbox],
            mailbox=mailbox,
            body_text="Your ticket was updated.\n\nThanks,\nSample Helpdesk\n",
        ),
    ]
    addressee = resolve_reply_addressee(mailbox=mailbox, messages=messages)
    assert addressee is not None
    assert addressee.salute_name == ""
