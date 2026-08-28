"""Automated mail is tagged from RFC signals and narrow locals, not the LLM.

Oracles are worked examples from staging: PHMSA listserv, Zendesk/SampleHelpdesk
tickets, Exchange OOO, and named counterparties. A production change that tags
Alex Taylor as a robot, or misses noreply@ / Auto-Submitted, must fail these.

RFC 3834 Auto-Submitted (not X-Auto-Response-Suppress) is the auto-reply
signal. RFC 2919 List-Id is a mailing list. RFC 2369 List-Unsubscribe alone
is not — Gmail/Yahoo 2024 and ticket systems put it on human agent mail.
"""

from __future__ import annotations

from app.core.automated_mail import is_automated_mail
from app.core.internal_mail import enrich_triage_flags
from app.models.schemas.dashboard import TriageFlags


def test_noreply_sender_is_automated() -> None:
    assert is_automated_mail(sender="noreply@vendor.example", subject="Your receipt")


def test_no_reply_and_mailer_daemon_are_automated() -> None:
    assert is_automated_mail(sender="no-reply@acme.example", subject="Confirm")
    assert is_automated_mail(sender="mailer-daemon@acme.example", subject="bounce")


def test_notifications_local_alone_is_not_automated() -> None:
    """Stripe/Zendesk-style notifications@ is transactional, not a robot local."""
    assert not is_automated_mail(
        sender="notifications@stripe.example",
        subject="Paid",
    )


def test_named_person_is_not_automated() -> None:
    assert not is_automated_mail(
        sender="kelvin@sample-site.example.com",
        subject="Can you review the packet?",
    )


def test_out_of_office_subject_is_automated_even_from_a_person() -> None:
    assert is_automated_mail(
        sender="jane@client.example",
        subject="Automatic reply: Re: Screening",
    )
    assert is_automated_mail(
        sender="jane@client.example",
        subject="Out of Office: until Monday",
    )


def test_alerts_and_monitor_locals_are_not_automated() -> None:
    """Role mailboxes are not robots. Subject 'Alert:' is also too broad."""
    assert not is_automated_mail(sender="alerts@vendor.example", subject="Disk full")
    assert not is_automated_mail(sender="monitor@ops.example", subject="Latency spike")
    assert not is_automated_mail(
        sender="ops@vendor.example",
        subject="Alert: payment sync failed",
    )


def test_enrich_flags_tags_automated_without_touching_spam() -> None:
    flags = TriageFlags(is_spam=False, has_action_items=False)
    enriched = enrich_triage_flags(
        flags,
        sender="noreply@vendor.example",
        mailbox="elise@sample-site.example.com",
        subject="Your weekly digest",
    )
    assert enriched is not None
    assert enriched.is_automated is True
    assert enriched.is_spam is False
    assert enriched.is_internal is False


def test_enrich_flags_creates_automated_tag_without_prior_triage() -> None:
    enriched = enrich_triage_flags(
        None,
        sender="noreply@vendor.example",
        mailbox="inquiries@sample-site.example.com",
        subject="Receipt",
    )
    assert enriched is not None
    assert enriched.is_automated is True
    assert enriched.is_internal is False


def test_list_unsubscribe_header_marks_govdelivery_as_automated() -> None:
    """PHMSA listserv: subscriptions local + List-Unsubscribe (RFC 2369)."""
    assert is_automated_mail(
        sender="phmsa.subscriptions@info.dot.gov",
        subject="Registration is open for the 2026 PHMSA Hazmat Multimodal Event",
        headers={
            "List-Unsubscribe": "<https://public.govdelivery.com/accounts/USPHMSA/unsubscriber/new>"
        },
    )


def test_list_id_marks_mailing_list_as_automated() -> None:
    """RFC 2919 List-Id identifies a mailing list even with a brand From."""
    assert is_automated_mail(
        sender="office@dot.gov",
        sender_display_name="Office of Hazardous Material Safety",
        subject="Hazmat bulletin",
        headers={"List-Id": "<phmsa.list.govdelivery.com>"},
    )


def test_zendesk_agent_with_list_unsubscribe_is_not_automated() -> None:
    """SampleHelpdesk/Zendesk injects List-Unsubscribe on named-agent tickets."""
    assert not is_automated_mail(
        sender="helpdesk@sample-helpdesk.example.com",
        sender_display_name="Alex Taylor (SampleHelpdesk)",
        subject="[SampleHelpdesk] Re: Applicant screening",
        headers={
            "List-Unsubscribe": "<mailto:unsub@sample-helpdesk.example.com>",
        },
    )


def test_named_person_with_list_headers_is_not_automated() -> None:
    """Ticket/SaaS List-Id is not a robot when From is a named person."""
    assert not is_automated_mail(
        sender="helpdesk@sample-helpdesk.example.com",
        sender_display_name="Alex Taylor (SampleHelpdesk)",
        subject="[SampleHelpdesk] Re: Applicant screening",
        headers={"List-Id": "<tickets.sample-helpdesk.example.com>"},
    )


def test_rfc8058_bulk_headers_alone_are_not_automated() -> None:
    """Gmail/Yahoo one-click headers are on tickets and marketing. Not a robot From."""
    assert not is_automated_mail(
        sender="news@vendor.example",
        sender_display_name="Vendor Newsletter",
        subject="August product update",
        headers={
            "List-Unsubscribe": "<https://vendor.example/unsub>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )


def test_rfc8058_with_named_agent_is_not_automated() -> None:
    assert not is_automated_mail(
        sender="helpdesk@sample-helpdesk.example.com",
        sender_display_name="Alex Taylor (SampleHelpdesk)",
        subject="[SampleHelpdesk] Re: Applicant",
        headers={
            "List-Unsubscribe": "<https://sample-helpdesk.example.com/unsub>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )


def test_x_auto_response_suppress_is_not_automated() -> None:
    """MS-OXCMAIL: sender asks Exchange not to OOO them. Most M365 human mail."""
    assert not is_automated_mail(
        sender="ruth.hooker@sample-lab-vendor.example.com",
        sender_display_name="Hooker, Ruth E",
        subject="SampleLab integration follow-up",
        headers={"X-Auto-Response-Suppress": "All"},
    )


def test_auto_submitted_auto_generated_is_automated() -> None:
    assert is_automated_mail(
        sender="jane@client.example",
        subject="Ticket received",
        headers={"Auto-Submitted": "auto-generated"},
    )


def test_auto_submitted_auto_replied_is_automated() -> None:
    assert is_automated_mail(
        sender="beau.norris@sample-lab-vendor.example.com",
        sender_display_name="Norris, Beau P",
        subject="Automatic reply: Re: integration",
        headers={"Auto-Submitted": "auto-replied"},
    )


def test_auto_submitted_no_is_not_automated_from_that_header() -> None:
    assert not is_automated_mail(
        sender="kelvin@sample-site.example.com",
        subject="Can you review the packet?",
        headers={"Auto-Submitted": "no"},
    )


def test_named_person_with_no_headers_is_not_automated() -> None:
    assert not is_automated_mail(
        sender="siddiq@sample-partner.example.com",
        subject="Invitation: IDME's Demo - 2nd Week",
        headers=None,
    )


def test_calendar_invite_from_a_person_is_not_automated() -> None:
    """meetingRequest is calendar mail, not a listserv — Haiku draft_needed handles RSVP."""
    assert not is_automated_mail(
        sender="siddiq@sample-partner.example.com",
        subject="Accepted: IDME's Demo - 2nd Week",
        headers={},
    )


def test_subscriptions_role_mailbox_is_automated() -> None:
    """GovDelivery-style subscriptions@ is a list mailbox, not a person."""
    assert is_automated_mail(
        sender="phmsa.subscriptions@info.dot.gov",
        subject="Registration is open for the 2026 PHMSA Hazmat Multimodal Event",
    )


def test_enrich_flags_stale_audit_does_not_brand_a_human_thread() -> None:
    """P3 Showcase: first inbound was usdot@ listserv; Steve's reply is human.

    Thread-level Automated is False because not every inbound is a robot.
    A year-old triage audit must not keep the badge on.
    """
    flags = TriageFlags(is_spam=False, has_action_items=False, is_automated=True)
    enriched = enrich_triage_flags(
        flags,
        sender="srusso@sample-information.example.com",
        mailbox="sampleagent@sample-site.example.com",
        subject="Re: You're Invited! Public-Private Partnerships (P3) Showcase",
        is_automated=False,
    )
    assert enriched is not None
    assert enriched.is_automated is False
    """Listservs with a brand From stay automated when ingest stored the flag."""
    flags = TriageFlags(is_spam=False, has_action_items=False, is_automated=True)
    enriched = enrich_triage_flags(
        flags,
        sender="phmsa.subscriptions@info.dot.gov",
        mailbox="sampleagent@sample-site.example.com",
        subject="Registration is open for the 2026 PHMSA Hazmat Multimodal Event",
    )
    assert enriched is not None
    assert enriched.is_automated is True


def test_enrich_flags_accepts_stored_boolean_without_prior_flags() -> None:
    enriched = enrich_triage_flags(
        None,
        sender="phmsa.subscriptions@info.dot.gov",
        mailbox="sampleagent@sample-site.example.com",
        subject="Registration is open",
        is_automated=True,
    )
    assert enriched is not None
    assert enriched.is_automated is True


def test_enrich_flags_accepts_list_headers_for_pretty_from() -> None:
    flags = TriageFlags(is_spam=False, has_action_items=False, is_automated=False)
    enriched = enrich_triage_flags(
        flags,
        sender="phmsa.subscriptions@info.dot.gov",
        mailbox="sampleagent@sample-site.example.com",
        subject="Registration is open",
        headers={
            "List-Unsubscribe": (
                "<https://public.govdelivery.com/accounts/USPHMSA/unsubscriber/new>"
            ),
        },
    )
    assert enriched is not None
    assert enriched.is_automated is True
