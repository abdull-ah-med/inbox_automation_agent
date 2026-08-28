"""Draft (letter) is optional. Teaching notes are not.

Oracles are staging-shaped: PHMSA listserv, Siddiq RSVP-only invite,
and an invite whose unique body asks Elise to do something besides RSVP.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.draft_needed import invite_has_personal_message, resolve_draft_needed
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema


def _email(**kwargs: object) -> EmailMessageSchema:
    base: dict[str, object] = {
        "message_id": "m1",
        "conversation_id": "c1",
        "mailbox": "sampleagent@sample-site.example.com",
        "sender": "siddiq@sample-partner.example.com",
        "subject": "Invitation: IDME's Demo - 2nd Week",
        "body_text": "Join with Google Meet",
        "received_at": datetime(2026, 8, 28, 6, 27, tzinfo=UTC),
        "direction": EmailDirectionEnum.INBOUND,
    }
    base.update(kwargs)
    return EmailMessageSchema.model_validate(base)


def _triage(**kwargs: object) -> TriageResultSchema:
    base: dict[str, object] = {
        "is_spam": False,
        "has_action_items": True,
        "action_items_summary": "Act",
        "needs_context": False,
        "draft_needed": True,
    }
    base.update(kwargs)
    return TriageResultSchema.model_validate(base)


def test_join_google_meet_invite_is_not_a_personal_message() -> None:
    assert invite_has_personal_message("Join with Google Meet") is False


def test_empty_invite_body_is_not_a_personal_message() -> None:
    assert invite_has_personal_message("") is False
    assert invite_has_personal_message(None) is False


def test_invite_ask_to_present_is_a_personal_message() -> None:
    assert (
        invite_has_personal_message(
            "Hi Elise, can you walk through the IDME demo for ten minutes after we join?"
        )
        is True
    )


_GENERIC_CALENDAR_CHROME = """
Abu Bakkar Siddiq has invited you to IDME's Demo - 2nd Week

When: Monday Aug 31, 2026 2:00pm - 2:30pm Eastern Time
Where: Google Meet
Who: siddiq@sample-partner.example.com, sampleagent@sample-site.example.com
Organizer: Abu Bakkar Siddiq
Join with Google Meet
https://meet.google.com/abc-defg-hij

View event details
Google Calendar
"""


def test_calendar_rsvp_chrome_is_not_a_personal_message() -> None:
    """Time, place, join link, and 'invited you' are not a written ask."""
    assert invite_has_personal_message(_GENERIC_CALENDAR_CHROME) is False


def test_invite_greeting_plus_note_is_a_personal_message() -> None:
    """Organizer prose counts even without a question mark."""
    assert (
        invite_has_personal_message(
            "Hi Elise, looking forward to walking you through the IDME demo after we join."
        )
        is True
    )


def test_empty_unique_body_still_sees_ask_in_full_body() -> None:
    email = _email(
        meeting_message_type="meetingRequest",
        unique_body_text="",
        body_text="Hi Elise, can you walk through the IDME demo after we join?",
        is_automated=False,
    )
    assert resolve_draft_needed(email=email, triage=_triage(draft_needed=False)) is True


def test_empty_unique_body_still_ignores_calendar_chrome() -> None:
    email = _email(
        meeting_message_type="meetingRequest",
        unique_body_text="",
        body_text=_GENERIC_CALENDAR_CHROME,
        is_automated=False,
    )
    assert resolve_draft_needed(email=email, triage=_triage(draft_needed=True)) is False


def test_automated_listserv_never_needs_a_letter() -> None:
    email = _email(
        sender="phmsa.subscriptions@info.dot.gov",
        subject="Registration is open for the 2026 PHMSA Hazmat Multimodal Event",
        body_text="Join us in Houston",
        is_automated=True,
    )
    assert resolve_draft_needed(email=email, triage=_triage(draft_needed=True)) is False


def test_generic_meeting_request_never_needs_a_letter() -> None:
    email = _email(
        meeting_message_type="meetingRequest",
        unique_body_text="Join with Google Meet",
        is_automated=False,
    )
    assert resolve_draft_needed(email=email, triage=_triage(draft_needed=True)) is False


def test_meeting_request_with_a_written_ask_needs_a_letter() -> None:
    email = _email(
        meeting_message_type="meetingRequest",
        unique_body_text=(
            "Hi Elise, can you walk through the IDME demo for ten minutes after we join?"
        ),
        is_automated=False,
    )
    assert resolve_draft_needed(email=email, triage=_triage(draft_needed=False)) is True


def test_ordinary_client_ask_keeps_haiku_letter() -> None:
    email = _email(
        sender="client@example.com",
        subject="Need the packet",
        body_text="Please send the intake packet today.",
        meeting_message_type=None,
        is_automated=False,
    )
    assert resolve_draft_needed(email=email, triage=_triage(draft_needed=True)) is True


def test_spam_never_needs_a_letter() -> None:
    email = _email(is_automated=True)
    assert (
        resolve_draft_needed(email=email, triage=_triage(is_spam=True, draft_needed=True)) is False
    )
