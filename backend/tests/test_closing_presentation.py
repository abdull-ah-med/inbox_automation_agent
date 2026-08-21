"""Closing inbound → finished presentation without badge contradiction.

When triage marks no_action and the body is a courtesy close, presentation
must show finished (no Action needed Now badge) while history retains triage.
"""

from __future__ import annotations

from app.core.closing_mail import looks_like_closing_mail
from app.core.thread_policy import ThreadPolicyInput, derive_presentation


def test_closing_body_with_no_action_state_is_finished_not_open_work() -> None:
    assert looks_like_closing_mail("Thanks, sounds good!")
    view = derive_presentation(
        ThreadPolicyInput(
            state="NO_ACTION",
            urgency="NORMAL",
            has_action_items=False,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
            closing_signal=True,
        )
    )
    assert view.is_finished is True
    assert view.open_work is False
    assert "Action needed" not in [b.label for b in view.badges_now]


def test_closing_signal_suggests_resolve_when_still_drafted() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="DRAFTED",
            urgency="NORMAL",
            has_action_items=True,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
            closing_signal=True,
        )
    )
    assert view.suggest_resolve_default is True
