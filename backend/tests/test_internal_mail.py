"""Internal company mail: same-domain senders are tagged and never spam.

Oracles are hand-checked address pairs, not production's domain splitter.
A production change that treats colleague@sample-site.example.com as spam, or
drops the internal flag, must fail these tests.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.internal_mail import (
    apply_internal_mail_policy,
    display_state_for_internal_mail,
    enrich_triage_flags,
    is_internal_sender,
)
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.dashboard import TriageFlags
from app.repositories.audit_repo import triage_flags_from_event


def test_same_mailbox_domain_is_internal() -> None:
    assert is_internal_sender(
        "kelvin@sample-site.example.com",
        "elise@sample-site.example.com",
    )


def test_display_name_and_case_still_internal() -> None:
    assert is_internal_sender(
        "Kelvin Holt <Kelvin@sample-site.example.com>",
        "elise@sample-site.example.com",
    )


def test_allowlisted_extra_domain_is_internal() -> None:
    assert is_internal_sender(
        "ops@sisterco.example",
        "elise@sample-site.example.com",
        extra_domains=["sisterco.example"],
    )


def test_body_keyword_is_irrelevant_to_internal_flag() -> None:
    """INTERNAL in a body is not an input to is_internal_sender."""
    assert not is_internal_sender(
        "customer@gmail.com",
        "elise@sample-site.example.com",
        extra_domains=[],
    )


def test_gmail_customer_is_not_internal() -> None:
    assert not is_internal_sender(
        "notarysdca@gmail.com",
        "info@sample-services.example.com",
    )


def test_mailbox_sending_itself_is_not_internal() -> None:
    """Our own Sent Items share the mailbox domain; that is not a colleague."""
    assert not is_internal_sender(
        "info@sample-services.example.com",
        "info@sample-services.example.com",
    )


def test_missing_or_unknown_sender_is_not_internal() -> None:
    assert not is_internal_sender("", "elise@sample-site.example.com")
    assert not is_internal_sender("unknown", "elise@sample-site.example.com")
    assert not is_internal_sender("not-an-email", "elise@sample-site.example.com")


def test_internal_policy_clears_llm_spam_flag() -> None:
    llm = TriageResultSchema.model_validate(
        {
            "is_spam": True,
            "spam_reason": "Looks like a newsletter",
            "has_action_items": False,
            "action_items_summary": None,
            "needs_context": False,
            "context_reason": None,
            "routing_category": "general",
        }
    )
    result = apply_internal_mail_policy(
        llm,
        sender="hr@sample-site.example.com",
        mailbox="elise@sample-site.example.com",
    )
    assert result.is_spam is False
    assert result.spam_reason is None
    assert result.routing_category == "internal"


def test_internal_policy_keeps_specific_routing_category() -> None:
    llm = TriageResultSchema.model_validate(
        {
            "is_spam": True,
            "spam_reason": "Automated junk",
            "has_action_items": True,
            "action_items_summary": "Review invoice",
            "needs_context": False,
            "context_reason": None,
            "routing_category": "billing",
        }
    )
    result = apply_internal_mail_policy(
        llm,
        sender="finance@sample-site.example.com",
        mailbox="elise@sample-site.example.com",
    )
    assert result.is_spam is False
    assert result.routing_category == "billing"


def test_external_policy_leaves_spam_untouched() -> None:
    llm = TriageResultSchema.model_validate(
        {
            "is_spam": True,
            "spam_reason": "Marketing blast",
            "has_action_items": False,
            "action_items_summary": None,
            "needs_context": False,
            "context_reason": None,
            "routing_category": "general",
        }
    )
    result = apply_internal_mail_policy(
        llm,
        sender="promo@spam.example",
        mailbox="elise@sample-site.example.com",
    )
    assert result.is_spam is True
    assert result.spam_reason == "Marketing blast"
    assert result.routing_category == "general"


def test_enrich_flags_tags_internal_and_clears_stored_spam() -> None:
    flags = TriageFlags(
        is_spam=True,
        spam_reason="Looks like a newsletter",
        has_action_items=False,
        outcome="triage.spam_discarded",
    )
    enriched = enrich_triage_flags(
        flags,
        sender="kelvin@sample-site.example.com",
        mailbox="elise@sample-site.example.com",
    )
    assert enriched is not None
    assert enriched.is_internal is True
    assert enriched.is_spam is False
    assert enriched.spam_reason is None


def test_enrich_flags_creates_internal_tag_without_prior_triage() -> None:
    enriched = enrich_triage_flags(
        None,
        sender="kelvin@sample-site.example.com",
        mailbox="elise@sample-site.example.com",
    )
    assert enriched is not None
    assert enriched.is_internal is True
    assert enriched.is_spam is False


def test_enrich_flags_does_not_invent_triage_for_external_sender() -> None:
    assert (
        enrich_triage_flags(
            None,
            sender="orders@vendor.example",
            mailbox="elise@sample-site.example.com",
        )
        is None
    )


def test_enrich_flags_does_not_tag_mailbox_self_as_internal() -> None:
    assert (
        enrich_triage_flags(
            None,
            sender="info@sample-services.example.com",
            mailbox="info@sample-services.example.com",
        )
        is None
    )


def test_internal_spam_state_displays_as_no_action() -> None:
    assert (
        display_state_for_internal_mail(
            "SPAM",
            sender="kelvin@sample-site.example.com",
            mailbox="elise@sample-site.example.com",
        )
        == "NO_ACTION"
    )


def test_external_spam_state_stays_spam() -> None:
    assert (
        display_state_for_internal_mail(
            "SPAM",
            sender="promo@spam.example",
            mailbox="elise@sample-site.example.com",
        )
        == "SPAM"
    )


def test_internal_drafted_state_is_unchanged() -> None:
    assert (
        display_state_for_internal_mail(
            "DRAFTED",
            sender="kelvin@sample-site.example.com",
            mailbox="elise@sample-site.example.com",
        )
        == "DRAFTED"
    )


def test_audit_event_exposes_internal_flag() -> None:
    event = SimpleNamespace(
        event_type="triage.action_needed",
        payload={"is_spam": False, "is_internal": True, "routing_category": "internal"},
    )
    flags = triage_flags_from_event(event)  # type: ignore[arg-type]
    assert flags.is_internal is True
    assert flags.routing_category == "internal"


def test_outbound_counterpart_is_the_to_recipient() -> None:
    from app.core.internal_mail import thread_counterpart

    assert (
        thread_counterpart(
            mailbox="info@sample-services.example.com",
            sender="info@sample-services.example.com",
            direction="outbound",
            to_recipients=["nealdavien@yahoo.com"],
        )
        == "nealdavien@yahoo.com"
    )


def test_inbound_counterpart_is_the_customer_sender() -> None:
    from app.core.internal_mail import thread_counterpart

    assert (
        thread_counterpart(
            mailbox="info@sample-services.example.com",
            sender="notarysdca@gmail.com",
            direction="inbound",
            to_recipients=["info@sample-services.example.com"],
        )
        == "notarysdca@gmail.com"
    )
