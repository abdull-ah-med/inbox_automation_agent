"""Map mailbox email addresses to stable UI keys."""

from __future__ import annotations

import re

from app.core.config import Settings
from app.core.exceptions import UnknownMailboxError
from app.core.sanitize import sanitize_user_text
from app.models.schemas.dashboard import MailboxKey

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


def owner_for_mailbox(
    email: str,
    owners: dict[str, str] | None = None,
) -> str | None:
    """Return the configured owner display name for a mailbox email, if any."""
    if not owners:
        return None
    needle = email.strip().lower()
    if not needle:
        return None
    return owners.get(needle)


def mailbox_label(
    email: str,
    key: str | None = None,
    *,
    owners: dict[str, str] | None = None,
) -> str:
    """Human-readable label for a mailbox."""
    owner = owner_for_mailbox(email, owners)
    if owner:
        return owner
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


def resolve_allowed_mailbox(settings: Settings, mailbox: str) -> str:
    """Resolve a UI mailbox key to an allowed email; raise otherwise."""
    email = resolve_mailbox_email(sanitize_user_text(mailbox.strip()), list(settings.mailbox_list))
    if email is None or not settings.mailbox_allowed(email):
        raise UnknownMailboxError("Mailbox not found")
    return email


def scoped_mailboxes(settings: Settings, mailbox: str | None) -> list[str]:
    """Return the allowed mailboxes for a request; empty/None mailbox → all allowed."""
    allowed = list(settings.mailbox_list)
    if mailbox is None or not mailbox.strip():
        return allowed
    return [resolve_allowed_mailbox(settings, mailbox)]
