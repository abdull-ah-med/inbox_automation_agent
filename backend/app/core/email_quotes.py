"""Strip quoted reply history and RFC signatures before embedding.

Quoted history in the newest message duplicates older content and buries the
actual reply. This pass is deterministic (no Talon) so embedding documents stay
stable. Bump ``EMBED_CLEAN_VERSION`` when heuristics change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Independent of ``CLEAN_VERSION`` (body_clean). Reembed script keys off this.
EMBED_CLEAN_VERSION: int = 2

_MIN_REMAINDER_CHARS = 20

_GMAIL_ON_WROTE_RE = re.compile(r"(?im)^\s*On .+ wrote:\s*$")
_OUTLOOK_ORIGINAL_RE = re.compile(r"(?im)^-{2,}\s*Original Message\s*-{2,}\s*$")
_RFC_SIGNATURE_RE = re.compile(r"(?m)^-- $")
_QUOTE_LINE_RE = re.compile(r"(?m)^>.*(?:\n|$)")
_ORIGINAL_MESSAGE_LOOSE_RE = re.compile(r"(^|\n)[-\s]*Original Message[-\s]*\s*\n", re.IGNORECASE)
_UNDERSCORE_SEP_RE = re.compile(r"(^|\n)_{10,}\s*\n")
_OUTLOOK_HEADERS_RE = re.compile(
    r"(^|\n)From:\s.+\n(?:\s*\n)?(?:Sent|Date):\s.+(?:\n(?:To|Cc|Bcc|Subject):.*)*\n",
    re.IGNORECASE,
)
_ON_WROTE_LOOSE_RE = re.compile(r"(^|\n)On .+ wrote:\s*\n", re.IGNORECASE)
# Zendesk prior-comment chrome: default avatar block or "follow-up to previous request".
_ZENDESK_DEFAULT_AVATAR_RE = re.compile(
    r"(^|\n)\[https?://[^\]]*default-avatar[^\]]*\]\s*\n",
    re.IGNORECASE,
)
# Agent avatars in {{ticket.comments_formatted}}: first photo introduces the
# newest comment; later system/photos lines introduce prior agent comments.
_ZENDESK_AGENT_PHOTO_RE = re.compile(
    r"(^|\n)\[https?://[^\]]*/system/photos/[^\]]*\]\s*\n",
    re.IGNORECASE,
)
_ZENDESK_FOLLOWUP_RE = re.compile(
    r"(^|\n)This is a follow-up to your previous request\b",
    re.IGNORECASE,
)

# Shared with frontend/web/src/lib/email-quote-patterns.ts (M14). Flags are
# separate so the generated TS file can `new RegExp(source, flags)`.
# zendeskAgentPhoto is matched on the *second* hit in split_quoted_history.
QUOTE_PATTERN_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("originalMessage", _ORIGINAL_MESSAGE_LOOSE_RE.pattern, "i"),
    ("underscoreSep", _UNDERSCORE_SEP_RE.pattern, ""),
    ("outlookHeaders", _OUTLOOK_HEADERS_RE.pattern, "i"),
    ("onWrote", _ON_WROTE_LOOSE_RE.pattern, "i"),
    ("zendeskDefaultAvatar", _ZENDESK_DEFAULT_AVATAR_RE.pattern, "i"),
    ("zendeskAgentPhoto", _ZENDESK_AGENT_PHOTO_RE.pattern, "i"),
    ("zendeskFollowUp", _ZENDESK_FOLLOWUP_RE.pattern, "i"),
)


def find_quote_boundary(text: str) -> int | None:
    """Return the earliest index of a Gmail/Outlook quote block, or None."""
    cut_at: int | None = None
    for pattern in (_GMAIL_ON_WROTE_RE, _OUTLOOK_ORIGINAL_RE):
        match = pattern.search(text)
        if match is None:
            continue
        if cut_at is None or match.start() < cut_at:
            cut_at = match.start()
    return cut_at


def _match_boundary_at(match: re.Match[str]) -> int:
    return match.start() + (len(match.group(1)) if match.lastindex and match.group(1) else 0)


def _second_zendesk_agent_photo_boundary(text: str) -> int | None:
    """Cut at the second system/photos avatar — the start of prior agent comments."""
    matches = list(_ZENDESK_AGENT_PHOTO_RE.finditer(text))
    if len(matches) < 2:
        return None
    return _match_boundary_at(matches[1])


def split_quoted_history(text: str) -> tuple[str, str | None]:
    """Split a plain-text body into the new reply and quoted history.

    Unlike ``strip_quoted_reply``, an empty new reply is valid: quote-only
    bodies keep ``quoted`` and return ``main=""`` instead of restoring the wall.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return normalized, None

    candidates: list[int] = []
    for pattern in (
        _ORIGINAL_MESSAGE_LOOSE_RE,
        _UNDERSCORE_SEP_RE,
        _OUTLOOK_HEADERS_RE,
        _ON_WROTE_LOOSE_RE,
        _ZENDESK_DEFAULT_AVATAR_RE,
        _ZENDESK_FOLLOWUP_RE,
    ):
        match = pattern.search(normalized)
        if match is None:
            continue
        candidates.append(_match_boundary_at(match))

    agent_photo_at = _second_zendesk_agent_photo_boundary(normalized)
    if agent_photo_at is not None:
        candidates.append(agent_photo_at)

    if not candidates:
        return normalized, None

    quote_start = min(candidates)
    main = normalized[:quote_start].rstrip()
    quoted = normalized[quote_start:].strip()
    if not quoted:
        return normalized, None
    return main, quoted


@dataclass(frozen=True)
class QuoteStrippedBody:
    text: str
    quotes_stripped: bool = False
    signature_stripped: bool = False
    removed_spans: tuple[str, ...] = ()


def strip_quoted_reply(body: str) -> QuoteStrippedBody:
    """Return body with quoted history and known signature blocks removed.

    If the remainder would be shorter than 20 characters, return the original
    so a quote-only message is still searchable.
    """
    original = body or ""
    if not original.strip():
        return QuoteStrippedBody(text=original)

    normalized = original.replace("\r\n", "\n").replace("\r", "\n")
    working = normalized
    removed: list[str] = []
    quotes_stripped = False
    signature_stripped = False

    cut_at = find_quote_boundary(working)
    if cut_at is not None:
        removed.append(working[cut_at:])
        working = working[:cut_at].rstrip()
        quotes_stripped = True

    without_quotes, quote_count = _QUOTE_LINE_RE.subn("", working)
    if quote_count:
        working = without_quotes
        quotes_stripped = True

    sig = _RFC_SIGNATURE_RE.search(working)
    if sig is not None:
        removed.append(working[sig.start() :])
        working = working[: sig.start()].rstrip()
        signature_stripped = True

    remainder = working.strip()
    if len(remainder) < _MIN_REMAINDER_CHARS:
        return QuoteStrippedBody(text=original)

    return QuoteStrippedBody(
        text=remainder,
        quotes_stripped=quotes_stripped,
        signature_stripped=signature_stripped,
        removed_spans=tuple(removed),
    )
