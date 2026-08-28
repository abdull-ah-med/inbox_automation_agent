"""Automated mail is tagged from sender/subject, not the LLM.

Oracles are hand-picked noreply/out-of-office addresses. A production
change that tags a named colleague, or misses noreply@, must fail these.
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
    assert is_automated_mail(sender="notifications@stripe.example", subject="Paid")


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


def test_alert_and_monitor_senders_are_automated() -> None:
    assert is_automated_mail(sender="alerts@vendor.example", subject="Disk full")
    assert is_automated_mail(sender="monitor@ops.example", subject="Latency spike")
    assert is_automated_mail(sender="ops@vendor.example", subject="Alert: payment sync failed")


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
    """PHMSA-shaped listserv: named local-part, List-Unsubscribe present."""
    assert is_automated_mail(
        sender="phmsa.subscriptions@info.dot.gov",
        subject="Registration is open for the 2026 PHMSA Hazmat Multimodal Event",
        headers={
            "List-Unsubscribe": "<https://public.govdelivery.com/accounts/USPHMSA/unsubscriber/new>"
        },
    )


def test_auto_submitted_auto_generated_is_automated() -> None:
    assert is_automated_mail(
        sender="jane@client.example",
        subject="Ticket received",
        headers={"Auto-Submitted": "auto-generated"},
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


def test_dotted_subscriptions_local_without_headers_is_not_automated() -> None:
    """Do not treat phmsa.subscriptions as a noreply local-part by splitting on dots."""
    assert not is_automated_mail(
        sender="phmsa.subscriptions@info.dot.gov",
        subject="Registration is open for the 2026 PHMSA Hazmat Multimodal Event",
    )


def test_enrich_flags_keeps_stored_automated_for_pretty_from() -> None:
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
