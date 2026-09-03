"""DraftAssistant auto-close and CC-observer oracles. Hand-counted recipient/body fixtures."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.draftassistant_resolve import (
    draftassistant_auto_close_decision,
    is_cc_observer,
    should_reopen_finished_thread,
)
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import EmailTriageState


def _email(**overrides: object) -> EmailMessageSchema:
    payload = {
        "message_id": "m1",
        "conversation_id": "c1",
        "mailbox": "elise@example.com",
        "sender": "sam@client.com",
        "subject": "Office birthday cake Friday",
        "body_text": "Bringing cake Friday for Jordan's birthday. Feel free to stop by.",
        "received_at": datetime(2026, 9, 1, tzinfo=UTC),
        "to_recipients": ["jordan@client.com"],
        "cc_recipients": ["elise@example.com", "team@client.com"],
    }
    payload.update(overrides)
    return EmailMessageSchema.model_validate(payload)


def test_cc_only_birthday_is_observer() -> None:
    assert is_cc_observer(_email()) is True


def test_inquiries_cc_lead_is_not_observer() -> None:
    email = _email(
        mailbox="inquiries@example.com",
        cc_recipients=["inquiries@example.com"],
        body_text="Can you quote a DOT audit package for three drivers?",
        subject="New lead — DOT package",
    )
    assert is_cc_observer(email) is False


def test_named_in_body_is_not_observer() -> None:
    email = _email(
        body_text="Elise, can you send the onboarding packet this week?",
    )
    assert is_cc_observer(email, owner_name="Elise") is False


def test_to_recipient_is_not_observer() -> None:
    email = _email(
        to_recipients=["elise@example.com"],
        cc_recipients=["team@client.com"],
    )
    assert is_cc_observer(email) is False


def test_courtesy_close_high_confidence_auto_closes() -> None:
    email = _email(
        subject="Re: Friday walkthrough",
        body_text="Sounds good, thanks! Let me know if you're unable to join Friday.",
        to_recipients=["elise@example.com"],
        cc_recipients=[],
    )
    state = EmailTriageState(
        original_email=email,
        thread_context=ThreadContextSchema(
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            subject=email.subject,
            messages=[email],
        ),
        triage=TriageResultSchema(
            is_spam=False,
            has_action_items=False,
            needs_context=False,
            draft_needed=False,
        ),
    )
    decision = draftassistant_auto_close_decision(state)
    assert decision is not None
    assert decision.reason == "courtesy_close"
    assert decision.confidence_tier == "high"
    assert decision.summary == "Courtesy close — sender thanked you; no reply needed."


def test_fyi_without_courtesy_or_cc_does_not_auto_close() -> None:
    email = _email(
        subject="Samba Safety access",
        body_text="I now have access to Samba Safety's website.",
        to_recipients=["elise@example.com"],
        cc_recipients=[],
    )
    state = EmailTriageState(
        original_email=email,
        thread_context=ThreadContextSchema(
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            subject=email.subject,
            messages=[email],
        ),
        triage=TriageResultSchema(
            is_spam=False,
            has_action_items=False,
            needs_context=False,
            draft_needed=False,
        ),
    )
    assert draftassistant_auto_close_decision(state) is None


def test_cc_observer_auto_closes() -> None:
    state = EmailTriageState(
        original_email=_email(),
        thread_context=ThreadContextSchema(
            conversation_id="c1",
            mailbox="elise@example.com",
            subject="Office birthday cake Friday",
            messages=[_email()],
        ),
        triage=TriageResultSchema(
            is_spam=False,
            has_action_items=False,
            needs_context=False,
            draft_needed=False,
        ),
    )
    decision = draftassistant_auto_close_decision(state)
    assert decision is not None
    assert decision.reason == "cc_observer"
    assert decision.summary == "CC observer — you were copied; no reply needed."


def test_action_items_never_auto_close() -> None:
    state = EmailTriageState(
        original_email=_email(
            body_text="Sounds good, thanks!",
            to_recipients=["elise@example.com"],
            cc_recipients=[],
        ),
        thread_context=ThreadContextSchema(
            conversation_id="c1",
            mailbox="elise@example.com",
            subject="Re: Friday",
            messages=[],
        ),
        triage=TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            needs_context=False,
            draft_needed=True,
        ),
    )
    assert draftassistant_auto_close_decision(state) is None


def test_finished_thread_reopens_only_when_new_inbound_has_action() -> None:
    assert should_reopen_finished_thread(prior_state="RESOLVED", has_action_items=True) is True
    assert should_reopen_finished_thread(prior_state="NO_ACTION", has_action_items=True) is True
    assert should_reopen_finished_thread(prior_state="RESOLVED", has_action_items=False) is False
    assert should_reopen_finished_thread(prior_state="DRAFTED", has_action_items=True) is False
