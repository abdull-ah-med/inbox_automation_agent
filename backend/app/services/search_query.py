"""Discord-style search filters parsed from the query string.

Known keys: from, contains, subject, direction, mailbox. Unknown ``key:value``
tokens stay in free text. Repeated ``from:`` / ``mailbox:`` / ``direction:``
values are OR candidates; ``contains:`` and ``subject:`` stack as AND.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.sanitize import sanitize_user_text

_HTML_TAG_RE = re.compile(r"<[^>]*>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FTS_KEEP_COLON_RE = re.compile(r"[&|!()*'\"]")
_FILTER_KEYS = "from|contains|subject|direction|mailbox"
_FILTER_RE = re.compile(
    rf'(?:^|\s)({_FILTER_KEYS}):\s*(?:"([^"]*)"|(?!(?:{_FILTER_KEYS}):)([^\s]+))',
    re.IGNORECASE,
)
_DANGLING_FILTER_RE = re.compile(
    rf"(?:^|\s)(?:{_FILTER_KEYS}):\s*",
    re.IGNORECASE,
)
_DIRECTION_ALIASES = {
    "inbound": "inbound",
    "in": "inbound",
    "incoming": "inbound",
    "outbound": "outbound",
    "out": "outbound",
    "sent": "outbound",
    "outgoing": "outbound",
}


def _strip_html_and_controls(text: str) -> str:
    cleaned = _HTML_TAG_RE.sub(" ", text or "")
    return _CONTROL_RE.sub("", cleaned)


def _sanitize_free_text(text: str) -> str:
    cleaned = _FTS_KEEP_COLON_RE.sub(" ", text)
    return " ".join(cleaned.split())


def _normalize_direction(value: str) -> str | None:
    return _DIRECTION_ALIASES.get(value.lower())


@dataclass(frozen=True)
class ParsedSearchQuery:
    free_text: str
    senders: tuple[str, ...]
    contains: tuple[str, ...]
    subjects: tuple[str, ...]
    directions: tuple[str, ...]
    mailboxes: tuple[str, ...]

    def is_empty(self) -> bool:
        return not (
            self.free_text
            or self.senders
            or self.contains
            or self.subjects
            or self.directions
            or self.mailboxes
        )

    def has_column_filters(self) -> bool:
        return bool(self.senders or self.contains or self.subjects or self.directions)


def parse_search_query(raw: str) -> ParsedSearchQuery:
    """Pull known ``key:value`` filters out; leftover tokens are free text."""
    text = _strip_html_and_controls(raw)
    senders: list[str] = []
    contains: list[str] = []
    subjects: list[str] = []
    directions: list[str] = []
    mailboxes: list[str] = []

    def _take(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        raw_value = match.group(2) if match.group(2) is not None else match.group(3)
        value = sanitize_user_text(raw_value or "")
        if not value:
            return " "
        if key == "from":
            senders.append(value)
        elif key == "contains":
            contains.append(value)
        elif key == "subject":
            subjects.append(value)
        elif key == "direction":
            direction = _normalize_direction(value)
            if direction is not None and direction not in directions:
                directions.append(direction)
        elif key == "mailbox":
            mailboxes.append(value)
        return " "

    remainder = _FILTER_RE.sub(_take, text)
    remainder = _DANGLING_FILTER_RE.sub(" ", remainder)
    return ParsedSearchQuery(
        free_text=_sanitize_free_text(remainder),
        senders=tuple(senders),
        contains=tuple(contains),
        subjects=tuple(subjects),
        directions=tuple(directions),
        mailboxes=tuple(mailboxes),
    )


def _quote_if_needed(value: str) -> str:
    if " " in value:
        return f'"{value}"'
    return value


def format_search_query(parsed: ParsedSearchQuery) -> str:
    """Canonical query string for API echo (no HTML, filters first)."""
    parts: list[str] = []
    for sender in parsed.senders:
        parts.append(f"from:{_quote_if_needed(sender)}")
    for term in parsed.contains:
        parts.append(f"contains:{_quote_if_needed(term)}")
    for subject in parsed.subjects:
        parts.append(f"subject:{_quote_if_needed(subject)}")
    for direction in parsed.directions:
        parts.append(f"direction:{direction}")
    for mailbox in parsed.mailboxes:
        parts.append(f"mailbox:{_quote_if_needed(mailbox)}")
    if parsed.free_text:
        parts.append(parsed.free_text)
    return " ".join(parts)
