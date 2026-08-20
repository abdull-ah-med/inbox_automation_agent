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

    cut_at: int | None = None
    gmail = _GMAIL_ON_WROTE_RE.search(working)
    outlook = _OUTLOOK_ORIGINAL_RE.search(working)
    for match in (gmail, outlook):
        if match is None:
            continue
        if cut_at is None or match.start() < cut_at:
            cut_at = match.start()
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
