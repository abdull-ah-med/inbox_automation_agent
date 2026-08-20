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


def test_undeliverable_subject_is_automated() -> None:
    assert is_automated_mail(
        sender="postmaster@client.example",
        subject="Undeliverable: Invoice 4412",
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
