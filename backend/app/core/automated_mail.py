"""Detect automated mail from RFC Auto-Submitted, noreply/list locals, and OOO subjects.

Automated is a per-message property. Noreply locals always win. A named
person is never a robot from List-* or Graph/Outlook headers alone.
Thread-level Automated is decided separately and stays off once a human
discussion or Elise reply exists.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.core.internal_mail import extract_email_address

_NOREPLY_LOCALS = frozenset(
    {
        "noreply",
        "no-reply",
        "no_reply",
        "no.reply",
        "donotreply",
        "do-not-reply",
        "do_not_reply",
        "mailer-daemon",
        "mailerdaemon",
        "postmaster",
        "bounce",
        "bounces",
        "autoresponder",
    }
)
_LIST_MAILBOX_LOCALS = frozenset(
    {
        "newsletter",
        "newsletters",
        "subscription",
        "subscriptions",
        "unsubscribe",
    }
)
_AUTOMATED_SUBJECT_PREFIXES = (
    "automatic reply",
    "auto-reply",
    "auto reply",
    "out of office",
    "undeliverable",
    "delivery status notification",
)
_NOREPLY_COMPACT = frozenset(
    item.replace(".", "").replace("-", "").replace("_", "") for item in _NOREPLY_LOCALS
)
# Greeting suppression still treats list mailboxes as non-person display tokens.
AUTOMATED_LOCAL_COMPACTS = _NOREPLY_COMPACT | frozenset(
    item.replace(".", "").replace("-", "").replace("_", "") for item in _LIST_MAILBOX_LOCALS
)
_PLUS_TAG = re.compile(r"\+.*$")
_AUTO_SUBMITTED_VALUES = frozenset({"auto-generated", "auto-replied", "auto-notified"})
_ORG_SECOND_TOKENS = frozenset(
    {
        "newsletter",
        "newsletters",
        "subscriptions",
        "subscription",
        "notifications",
        "notification",
        "support",
        "team",
        "office",
        "department",
        "helpdesk",
        "noreply",
        "bounce",
        "mailer",
        "services",
        "safety",
        "of",
        "for",
        "the",
        "and",
    }
)
_LAST_FIRST = re.compile(r"^[A-Za-z][A-Za-z'\-]+,\s+[A-Z][A-Za-z]")
_NAME_WITH_BRAND = re.compile(
    r"^([A-Za-z][A-Za-z'\-]+(?:\s+[A-Za-z][A-Za-z'\-.]+){1,3})\s*\([^)]+\)\s*$"
)


def _local_part(sender: str | None) -> str | None:
    address = extract_email_address(sender)
    if address is None or "@" not in address:
        return None
    local, _, _domain = address.partition("@")
    local = _PLUS_TAG.sub("", local).strip().lower()
    return local or None


def _normalize_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {
        str(name).strip().lower(): str(value).strip()
        for name, value in headers.items()
        if name and str(name).strip()
    }


def _is_list_mailbox(local: str | None) -> bool:
    if local is None:
        return False
    if local in _LIST_MAILBOX_LOCALS:
        return True
    if "." in local:
        last = local.rsplit(".", 1)[-1]
        return last in _LIST_MAILBOX_LOCALS
    return False


def _tokens_look_like_person(name: str) -> bool:
    tokens = [part for part in name.split() if part]
    if len(tokens) < 2:
        return False
    first, second = tokens[0], tokens[1]
    if first.isupper() and len(first) > 1:
        return False
    if first.lower() in _ORG_SECOND_TOKENS or second.lower() in _ORG_SECOND_TOKENS:
        return False
    return first[0].isalpha() and first[0].isupper() and second[0].isalpha() and second[0].isupper()


def _person_shaped_from(display_name: str | None, sender: str | None) -> bool:
    """True for 'Alex Taylor (SampleHelpdesk)' or Exchange 'Hooker, Ruth E'."""
    _ = sender
    name = (display_name or "").strip().strip("\"'")
    if not name:
        return False
    if _LAST_FIRST.match(name):
        return True
    branded = _NAME_WITH_BRAND.match(name)
    if branded is not None:
        return _tokens_look_like_person(branded.group(1))
    return _tokens_look_like_person(name)


def is_automated_mail(
    *,
    sender: str | None,
    subject: str | None = None,
    headers: Mapping[str, str] | None = None,
    sender_display_name: str | None = None,
) -> bool:
    """True for RFC auto-replies, noreply/list mailboxes, and OOO subjects.

    A named person is never tagged from List-* / Graph headers. Noreply
    locals still win when Teams (or similar) puts a chat name in From.
    Outlook X-Auto-Response-Suppress and List-Unsubscribe-Post are ignored.
    """
    normalized = _normalize_headers(headers)
    auto_submitted = normalized.get("auto-submitted", "").lower()
    if auto_submitted and auto_submitted != "no":
        first = auto_submitted.split(";", 1)[0].strip()
        if first in _AUTO_SUBMITTED_VALUES:
            return True

    text = (subject or "").strip().lower()
    if any(text.startswith(prefix) for prefix in _AUTOMATED_SUBJECT_PREFIXES):
        return True

    local = _local_part(sender)
    if local is not None and local in _NOREPLY_LOCALS:
        return True
    if local is not None:
        compact = local.replace(".", "").replace("-", "").replace("_", "")
        if compact in _NOREPLY_COMPACT:
            return True

    # Person-shaped From only vetoes list/List-Id signals (Zendesk agents),
    # not noreply — Teams uses no-reply@ with the participant as display name.
    if _person_shaped_from(sender_display_name, sender):
        return False

    if _is_list_mailbox(local):
        return True
    return "list-id" in normalized
