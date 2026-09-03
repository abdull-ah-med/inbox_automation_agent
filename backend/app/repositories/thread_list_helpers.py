"""Shared helpers for thread list/summary SQL rows (keeps thread_repo under LOC budget)."""

from __future__ import annotations

from app.core.internal_mail import (
    display_state_for_internal_mail,
    enrich_triage_flags,
    thread_counterpart,
)
from app.models.schemas.dashboard import TriageFlags


def enriched_triage(
    flags: TriageFlags | None,
    *,
    mailbox: str,
    last_sender: str | None,
    subject: str | None = None,
    is_automated: bool | None = None,
) -> TriageFlags | None:
    return enrich_triage_flags(
        flags,
        sender=last_sender,
        mailbox=mailbox,
        subject=subject,
        is_automated=is_automated,
    )


def display_state(state: str, *, mailbox: str, last_sender: str | None) -> str:
    return display_state_for_internal_mail(state, sender=last_sender, mailbox=mailbox)


def card_preview(
    body_text: str | None, unique_body_text: str | None, body_preview: str | None
) -> str | None:
    from app.services.thread_view_service import preview_text_for_message

    return preview_text_for_message(
        body_text=body_text or "", unique_body_text=unique_body_text, body_preview=body_preview
    )


def party_sender(
    mailbox: str,
    sender: str | None,
    direction: str | None,
    to_recipients: list[str] | None,
) -> str | None:
    return thread_counterpart(
        mailbox=mailbox,
        sender=sender,
        direction=direction,
        to_recipients=list(to_recipients or []),
    )
