"""Keep thread facts operational; refer to people by name, not email."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.core.person_name import display_name_first_name
from app.core.reply_addressee import participant_first_names

_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_MEDICAL = re.compile(
    r"\b(appendicitis|hospitalized|surgery|diagnosed|chemotherapy|pregnant)\b",
    re.I,
)
_FAMILY_HEALTH = re.compile(
    r"\b(son|daughter|wife|husband|child)\b.{0,40}\b(had|has|sick|ill|hospital)",
    re.I,
)
_SENT_SOMETHING = re.compile(r"\bsent something\b", re.I)
_SIGNATURE = re.compile(
    r"(director at|\bext\.?\s*\d+|\bphone\s*\(?\d|\bwith email\b.{0,80}\bphone\b)",
    re.I,
)
_JOB_TITLE = re.compile(
    r"\bis\s+(?:the\s+)?(?:director|vice president|\bvp\b|manager|ceo|cto|cfo|"
    r"president|head)\b",
    re.I,
)
_HELPDESK_FILLER = re.compile(
    r"\b(needs? urgent resolution|need workaround or resolution|"
    r"asking if help is available|is requesting help to resolve)\b",
    re.I,
)
_ANONYMOUS_ACTOR = re.compile(
    r"^(an applicant|the applicant|a client|the client|the user|a user)\b",
    re.I,
)
_VAGUE_ISSUE = re.compile(
    r"\b(site issues?|security issues?|experiencing .{0,40}issues?)\b",
    re.I,
)
_NAMED_PRODUCT = re.compile(
    r"\b(appscreen|myapp|quickbooks|qbo|salesforce|okta)\b",
    re.I,
)
_VAGUE_RESOLVED = re.compile(r"\bis resolved\b", re.I)
_HAS_DIGIT = re.compile(r"\d")
_ALWAYS_REJECT = (
    _MEDICAL,
    _FAMILY_HEALTH,
    _SENT_SOMETHING,
    _SIGNATURE,
    _JOB_TITLE,
    _HELPDESK_FILLER,
    _ANONYMOUS_ACTOR,
)
_IDENTITY_FACT = re.compile(
    r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+is\b.{0,160}?\bemail\s+"
    r"([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})",
    re.I | re.S,
)


def is_useful_thread_fact(text: str) -> bool:
    """True when the sentence can change a draft: id, ask, decision, blocker."""
    body = (text or "").strip()
    if len(body) < 24:
        return False
    if any(pattern.search(body) for pattern in _ALWAYS_REJECT):
        return False
    has_anchor = bool(_HAS_DIGIT.search(body) or _NAMED_PRODUCT.search(body))
    if re.match(r"^client\b", body, re.I) and not has_anchor:
        return False
    if _VAGUE_ISSUE.search(body) and not has_anchor:
        return False
    return not (_VAGUE_RESOLVED.search(body) and not _HAS_DIGIT.search(body))


def person_aliases_from_messages(
    messages: Iterable[Any],
    *,
    mailbox: str | None = None,
    mailbox_owner: str | None = None,
    directory: Mapping[str, str] | None = None,
) -> dict[str, str]:
    return participant_first_names(
        mailbox=mailbox,
        messages=list(messages),
        mailbox_owner=mailbox_owner,
        directory=directory,
    )


def person_aliases_from_fact_texts(texts: Iterable[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for text in texts:
        match = _IDENTITY_FACT.search(text or "")
        if match is None:
            continue
        first = display_name_first_name(match.group(1).strip())
        email = match.group(2).strip().lower()
        if first and email:
            aliases[email] = first
    return aliases


def apply_person_aliases(text: str, aliases: dict[str, str]) -> str:
    """Replace emails with names except immediately after the word email."""
    if not text or not aliases:
        return text
    result = text
    for email, name in sorted(aliases.items(), key=lambda item: -len(item[0])):
        pattern = re.compile(rf"(?<!email )(?<!email: ){re.escape(email)}", re.I)
        result = pattern.sub(name, result)
    return result
