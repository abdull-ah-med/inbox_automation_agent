"""Detect company-internal mail from sender vs mailbox domain."""

from __future__ import annotations

import re

from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.dashboard import TriageFlags
from app.models.schemas.email import ThreadStateEnum

_ANGLE_EMAIL = re.compile(r"<([^<>@\s]+@[^<>@\s]+)>")


def extract_email_address(raw: str | None) -> str | None:
    """Return a lowercase addr from a raw sender/mailbox string, or None."""
    if raw is None:
        return None
    text = raw.strip()
    if not text or text.lower() == "unknown":
        return None
    angled = _ANGLE_EMAIL.search(text)
    if angled:
        return angled.group(1).strip().lower()
    if "@" not in text:
        return None
    return text.strip().strip("\"'").lower()


def email_domain(raw: str | None) -> str | None:
    address = extract_email_address(raw)
    if address is None:
        return None
    _, _, domain = address.partition("@")
    domain = domain.strip().lower()
    return domain or None


def is_internal_sender(
    sender: str | None,
    mailbox: str | None,
    *,
    extra_domains: list[str] | None = None,
) -> bool:
    """True when a *colleague* shares the mailbox domain or an allowlisted domain.

    The mailbox addressing itself (Sent Items / outbound) is not internal.
    Body keywords like INTERNAL are never consulted.
    """
    sender_addr = extract_email_address(sender)
    mailbox_addr = extract_email_address(mailbox)
    if sender_addr is None or mailbox_addr is None:
        return False
    if sender_addr == mailbox_addr:
        return False
    sender_domain = email_domain(sender)
    mailbox_domain = email_domain(mailbox)
    if sender_domain is None:
        return False
    allowed = {mailbox_domain} if mailbox_domain else set()
    for domain in extra_domains or []:
        cleaned = domain.strip().lower().lstrip("@")
        if cleaned:
            allowed.add(cleaned)
    return sender_domain in allowed


def thread_counterpart(
    *,
    mailbox: str | None,
    sender: str | None,
    direction: str | None,
    to_recipients: list[str] | None = None,
) -> str | None:
    """The other party on a thread, never the mailbox itself.

    Inbound → sender. Outbound (or self-sender) → first To that is not us.
    """
    mailbox_addr = extract_email_address(mailbox)
    sender_addr = extract_email_address(sender)
    direction_norm = (direction or "").strip().lower()
    if (
        direction_norm == "inbound"
        and sender_addr is not None
        and sender_addr != mailbox_addr
    ):
        text = (sender or "").strip()
        return text or sender_addr
    for recipient in to_recipients or []:
        rec_addr = extract_email_address(recipient)
        if rec_addr is not None and rec_addr != mailbox_addr:
            text = recipient.strip()
            return text or rec_addr
    if sender_addr is not None and sender_addr != mailbox_addr:
        text = (sender or "").strip()
        return text or sender_addr
    return None


def apply_internal_mail_policy(
    triage: TriageResultSchema,
    *,
    sender: str,
    mailbox: str,
    extra_domains: list[str] | None = None,
) -> TriageResultSchema:
    """Internal senders are never spam; general routing becomes internal."""
    if not is_internal_sender(sender, mailbox, extra_domains=extra_domains):
        return triage
    updates: dict[str, object] = {}
    if triage.is_spam:
        updates["is_spam"] = False
        updates["spam_reason"] = None
    if triage.routing_category == "general":
        updates["routing_category"] = "internal"
    if not updates:
        return triage
    return triage.model_copy(update=updates)


def enrich_triage_flags(
    flags: TriageFlags | None,
    *,
    sender: str | None,
    mailbox: str | None,
    subject: str | None = None,
    extra_domains: list[str] | None = None,
) -> TriageFlags | None:
    """Attach internal/automated tags and clear a stored spam flag on internal mail."""
    from app.core.automated_mail import is_automated_mail

    domains = extra_domains
    if domains is None:
        try:
            from app.core.config import get_settings

            domains = get_settings().internal_domain_list
        except Exception:
            domains = []

    internal = is_internal_sender(sender, mailbox, extra_domains=domains)
    automated = is_automated_mail(sender=sender, subject=subject)
    if flags is None:
        if not internal and not automated:
            return None
        return TriageFlags(
            is_internal=internal,
            is_automated=automated,
            is_spam=False if internal else None,
        )
    updates: dict[str, object] = {
        "is_internal": internal,
        "is_automated": automated,
    }
    if internal and flags.is_spam:
        updates["is_spam"] = False
        updates["spam_reason"] = None
    return flags.model_copy(update=updates)


def display_state_for_internal_mail(
    state: str,
    *,
    sender: str | None,
    mailbox: str | None,
    extra_domains: list[str] | None = None,
) -> str:
    """Internal mail stored as SPAM is shown as NO_ACTION, not spam."""
    domains = extra_domains
    if domains is None:
        try:
            from app.core.config import get_settings

            domains = get_settings().internal_domain_list
        except Exception:
            domains = []
    if state == ThreadStateEnum.SPAM.value and is_internal_sender(
        sender, mailbox, extra_domains=domains
    ):
        return ThreadStateEnum.NO_ACTION.value
    return state
