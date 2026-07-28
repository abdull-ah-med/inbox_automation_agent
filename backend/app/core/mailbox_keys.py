"""Map mailbox email addresses to stable UI keys."""

from __future__ import annotations

import re

from app.models.schemas.dashboard import MAILBOX_KEYS, MailboxKey

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify_local(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    slug = _SLUG_RE.sub("-", local).strip("-")
    return slug or email.strip().lower()


def infer_mailbox_key(email: str) -> MailboxKey | str:
    """Derive a mailbox key from an email local-part.

    Known demo-style names keep their semantic keys; everything else uses the
    local-part slug so real TARGET_MAILBOXES (inquiries, support, …) work.
    """
    local = email.split("@", 1)[0].lower().replace(".", "").replace("-", "").replace("_", "")
    if "client" in local or "relation" in local:
        return "client-relations"
    if "sales" in local:
        return "sales"
    if "vendor" in local:
        return "vendor"
    if "intermed" in local or "partner" in local:
        return "intermediary"
    return _slugify_local(email)


def mailbox_label(email: str, key: str | None = None) -> str:
    """Human-readable label for a mailbox."""
    resolved = key or infer_mailbox_key(email)
    labels = {
        "client-relations": "Client Relations",
        "sales": "Sales",
        "vendor": "Vendor",
        "intermediary": "Intermediary",
    }
    if resolved in labels:
        return labels[resolved]
    local = email.split("@", 1)[0]
    return local.replace(".", " ").replace("-", " ").replace("_", " ").title()


def resolve_mailbox_email(mailbox_key: str, mailbox_emails: list[str]) -> str | None:
    """Find the configured email address for a UI mailbox key."""
    needle = mailbox_key.strip().lower()
    if "@" in needle:
        for email in mailbox_emails:
            if email.lower() == needle:
                return email
        return None
    for email in mailbox_emails:
        if infer_mailbox_key(email) == needle:
            return email
    return None


def is_known_mailbox_key(value: str) -> bool:
    return value in MAILBOX_KEYS
