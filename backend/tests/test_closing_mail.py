"""Closing-mail heuristics: courtesy closes are finished, asks are not.

Oracles are hand-picked phrases matching the frontend courtesy-close contract.
"""

from __future__ import annotations

from app.core.closing_mail import looks_like_closing_mail


def test_thanks_sounds_good_is_closing() -> None:
    assert looks_like_closing_mail("Thanks, sounds good!")
    assert looks_like_closing_mail("We are all set on our end. Appreciate it.")


def test_conditional_unable_is_closing() -> None:
    assert looks_like_closing_mail(
        "Let me know if you're unable to join and we will reschedule."
    )


def test_explicit_ask_is_not_closing() -> None:
    assert not looks_like_closing_mail("Can you please send the onboarding packet?")
    assert not looks_like_closing_mail("What is the status on the payment sync?")


def test_empty_body_is_not_closing() -> None:
    assert not looks_like_closing_mail("")
    assert not looks_like_closing_mail("   ")
