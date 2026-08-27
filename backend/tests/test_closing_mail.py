"""Closing-mail heuristics: courtesy closes are finished, asks are not.

Oracles are hand-picked phrases matching the frontend courtesy-close contract.
"""

from __future__ import annotations

from app.core.closing_mail import looks_like_closing_mail


def test_thanks_sounds_good_is_closing() -> None:
    assert looks_like_closing_mail("Thanks, sounds good!")
    assert looks_like_closing_mail("We are all set on our end. Appreciate it.")
    assert looks_like_closing_mail("Thank you!")


def test_conditional_unable_is_closing() -> None:
    assert looks_like_closing_mail("Let me know if you're unable to join and we will reschedule.")


def test_explicit_ask_is_not_closing() -> None:
    assert not looks_like_closing_mail("Can you please send the onboarding packet?")
    assert not looks_like_closing_mail("What is the status on the payment sync?")


def test_empty_body_is_not_closing() -> None:
    assert not looks_like_closing_mail("")
    assert not looks_like_closing_mail("   ")


# Worked example: Sample Case 70300 follow-up (latest inbound asks to proceed;
# quoted prior email starts with "Thank you again").
_METHOD_PS_FOLLOW_UP = (
    "Hi Elise and Brad,\n"
    "\n"
    "I wanted to follow up on my previous email regarding the payment sync "
    "issues and the complimentary hour of Professional Services time we "
    "offered to help review your customized screens.\n"
    "\n"
    "Please let me know if you would like to proceed with this, and I will "
    "be happy to coordinate the next steps with our team.\n"
    "\n"
    "Best regards,\n"
    "\n"
    "On Thu, Aug 20, 2026 at 11:43 AM Smit Patel <smit.patel@sample-transport.example.com> wrote:\n"
    "\n"
    "Thank you again for taking the time to speak with me regarding the "
    "payment sync issues.\n"
)

_COURTESY_ABOVE_QUOTED_ASK = (
    "Sounds good, thanks! We are all set.\n"
    "\n"
    "On Thu, Aug 20, 2026 at 11:43 AM Smit Patel <smit.patel@sample-transport.example.com> wrote:\n"
    "\n"
    "Can you please send the onboarding packet?\n"
)


def test_quoted_thank_you_does_not_make_a_follow_up_a_close() -> None:
    assert not looks_like_closing_mail(_METHOD_PS_FOLLOW_UP)


def test_courtesy_close_above_quoted_ask_is_still_closing() -> None:
    assert looks_like_closing_mail(_COURTESY_ABOVE_QUOTED_ASK)


# Worked example: Karol Duarte 8/24 follow-up on the content calendar
# (thread 1b7d02b5-7b16-44a9-9976-3f6e05fdef37). Asks for approval; "Thank you!"
# is a sign-off, not a courtesy close. Spanish Outlook quote sits below.
_KAROL_CALENDAR_FOLLOW_UP = (
    "Hi Elise and Jodi,\n"
    "Just following up on Weeks 3 and 4 of the SampleSite content calendar\n"
    "No rush, but if possible, it would be great to have your approval by the "
    "end of the week so we can keep things moving\n"
    "Thank you!\n"
    "\n"
    "Karol Duarte\n"
    "Digital Marketing Coordinator\n"
    "\n"
    "________________________________\n"
    "De: Karol Duarte\n"
    "Enviado: martes, 18 de agosto de 2026 15:23\n"
    "Asunto: Content Calendar - (FB-IG-LK)\n"
    "\n"
    "Hi Elise and Jodi,\n"
    "I\u2019m sharing Weeks 3 and 4 of the SampleSite content calendar for your review\n"
    "Thank you!\n"
)


def test_follow_up_that_ends_with_thank_you_is_not_closing() -> None:
    assert not looks_like_closing_mail(_KAROL_CALENDAR_FOLLOW_UP)
