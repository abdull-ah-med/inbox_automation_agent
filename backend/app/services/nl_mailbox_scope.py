"""Peel natural-language mailbox scope out of free text before FTS/embed.

``info mailbox`` / ``inquiries inbox`` / a known full address become mailbox
filter tokens. Content keywords stay. Unknown ``finance mailbox`` is left
alone so FTS is not fed invented allowlist entries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.mailbox_keys import infer_mailbox_key

_SCOPE_NOUNS = ("mailbox", "inbox", "folder")


@dataclass(frozen=True)
class NlMailboxScope:
    mailboxes: tuple[str, ...]
    free_text: str


def _alias_map(mailbox_emails: list[str]) -> dict[str, str]:
    """Map user-facing aliases → token ``resolve_mailbox_email`` accepts."""
    aliases: dict[str, str] = {}
    for email in mailbox_emails:
        cleaned = (email or "").strip()
        if not cleaned or "@" not in cleaned:
            continue
        local = cleaned.split("@", 1)[0].lower()
        key = str(infer_mailbox_key(cleaned)).lower()
        for alias in {local, key}:
            if alias:
                aliases[alias] = local
        aliases[cleaned.lower()] = cleaned
    return aliases


def extract_nl_mailbox_scope(
    free_text: str,
    mailbox_emails: list[str],
) -> NlMailboxScope:
    """Extract known mailbox phrases from free text; leave content keywords."""
    text = " ".join((free_text or "").split())
    if not text or not mailbox_emails:
        return NlMailboxScope(mailboxes=(), free_text=text)

    aliases = _alias_map(mailbox_emails)
    if not aliases:
        return NlMailboxScope(mailboxes=(), free_text=text)

    alias_pattern = "|".join(
        re.escape(alias) for alias in sorted(aliases.keys(), key=len, reverse=True)
    )
    nouns = "|".join(_SCOPE_NOUNS)
    found: list[str] = []
    remainder = text

    phrase_patterns = (
        re.compile(
            rf"\b({alias_pattern})\s+(?:{nouns})\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:{nouns})\s+(?:for\s+)?({alias_pattern})\b",
            re.IGNORECASE,
        ),
    )
    for pattern in phrase_patterns:
        while True:
            match = pattern.search(remainder)
            if match is None:
                break
            raw_alias = match.group(1).lower()
            token = aliases.get(raw_alias)
            if token is None:
                break
            if token not in found:
                found.append(token)
            remainder = (remainder[: match.start()] + " " + remainder[match.end() :]).strip()
            remainder = " ".join(remainder.split())

    email_aliases = [alias for alias in aliases if "@" in alias]
    if email_aliases:
        email_pattern = re.compile(
            r"\b("
            + "|".join(re.escape(a) for a in sorted(email_aliases, key=len, reverse=True))
            + r")\b",
            re.IGNORECASE,
        )
        while True:
            match = email_pattern.search(remainder)
            if match is None:
                break
            raw = match.group(1).lower()
            token = aliases.get(raw, raw)
            if token not in found:
                found.append(token)
            remainder = (remainder[: match.start()] + " " + remainder[match.end() :]).strip()
            remainder = " ".join(remainder.split())

    return NlMailboxScope(mailboxes=tuple(found), free_text=remainder)
