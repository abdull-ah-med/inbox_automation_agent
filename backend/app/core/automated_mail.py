"""Detect automated mail from sender, subject, and RFC internet headers."""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.core.internal_mail import extract_email_address

_AUTOMATED_LOCALS = frozenset(
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
        "notifications",
        "notification",
        "newsletter",
        "newsletters",
        "subscription",
        "subscriptions",
        "unsubscribe",
        "autoresponder",
        "automated",
        "daemon",
        "alerts",
        "alert",
        "monitor",
        "monitoring",
        "status",
        "system",
        "robot",
        "bots",
    }
)
_AUTOMATED_SUBJECT_PREFIXES = (
    "automatic reply",
    "auto-reply",
    "auto reply",
    "out of office",
    "undeliverable",
    "delivery status notification",
    "auto:",
    "alert:",
    "[alert]",
    "[monitoring]",
    "cron:",
)
_AUTOMATED_LOCALS_COMPACT = frozenset(
    item.replace(".", "").replace("-", "").replace("_", "") for item in _AUTOMATED_LOCALS
)
AUTOMATED_LOCAL_COMPACTS = _AUTOMATED_LOCALS_COMPACT
_PLUS_TAG = re.compile(r"\+.*$")

# RFC 2369 / 2919 / 8058 list headers — presence means bulk/list mail.
_LIST_HEADER_NAMES = frozenset(
    {
        "list-unsubscribe",
        "list-unsubscribe-post",
        "list-id",
        "list-subscribe",
        "list-post",
        "list-help",
        "list-owner",
        "list-archive",
    }
)
_PRECEDENCE_BULK = frozenset({"bulk", "list", "junk"})
_AUTO_SUBMITTED_VALUES = frozenset({"auto-generated", "auto-replied", "auto-notified"})


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


def _headers_indicate_automated(headers: Mapping[str, str] | None) -> bool:
    normalized = _normalize_headers(headers)
    if not normalized:
        return False
    if any(name in _LIST_HEADER_NAMES for name in normalized):
        return True
    auto_submitted = normalized.get("auto-submitted", "").lower()
    if auto_submitted and auto_submitted != "no":
        first = auto_submitted.split(";", 1)[0].strip()
        if first in _AUTO_SUBMITTED_VALUES:
            return True
    precedence = normalized.get("precedence", "").lower()
    if precedence in _PRECEDENCE_BULK:
        return True
    return "x-auto-response-suppress" in normalized


def is_automated_mail(
    *,
    sender: str | None,
    subject: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> bool:
    """True for list/auto RFC headers, noreply-style senders, and auto-reply subjects."""
    if _headers_indicate_automated(headers):
        return True
    local = _local_part(sender)
    if local is not None and local in _AUTOMATED_LOCALS:
        return True
    if local is not None:
        compact = local.replace(".", "").replace("-", "").replace("_", "")
        if compact in _AUTOMATED_LOCALS_COMPACT:
            return True
    text = (subject or "").strip().lower()
    return any(text.startswith(prefix) for prefix in _AUTOMATED_SUBJECT_PREFIXES)
