"""Detect automated mail from sender local-part and subject, no LLM."""

from __future__ import annotations

import re

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
_PLUS_TAG = re.compile(r"\+.*$")


def _local_part(sender: str | None) -> str | None:
    address = extract_email_address(sender)
    if address is None or "@" not in address:
        return None
    local, _, _domain = address.partition("@")
    local = _PLUS_TAG.sub("", local).strip().lower()
    return local or None


def is_automated_mail(*, sender: str | None, subject: str | None = None) -> bool:
    """True for noreply-style senders and auto-reply / bounce subjects."""
    local = _local_part(sender)
    if local is not None and local in _AUTOMATED_LOCALS:
        return True
    if local is not None:
        compact = local.replace(".", "").replace("-", "").replace("_", "")
        if compact in _AUTOMATED_LOCALS_COMPACT:
            return True
    text = (subject or "").strip().lower()
    return any(text.startswith(prefix) for prefix in _AUTOMATED_SUBJECT_PREFIXES)
