"""Deterministic closing / courtesy-close detection (shared with frontend contract)."""

from __future__ import annotations

import re

from app.utils.email_quotes import strip_quoted_reply

_ASK_RE = re.compile(
    r"\b(please (send|review|confirm|process|reply)|can you|could you|need you to|"
    r"what is the status)\b",
    re.IGNORECASE,
)
_CLOSE_RE = re.compile(
    r"\b(thanks|thank you|sounds good|appreciate it|we are all set|all set)\b",
    re.IGNORECASE,
)
_CONDITIONAL_RE = re.compile(
    r"let me know if you(?:'re| are) unable",
    re.IGNORECASE,
)
_OUTLOOK_SEP_RE = re.compile(r"(?im)^\s*_{5,}\s*$")
_THANKS_LINE_RE = re.compile(
    r"(?im)^\s*(?:thanks|thank you)[!.,]?\s*$",
)


def _newest_reply(body: str) -> str:
    text = strip_quoted_reply(body).text
    sep = _OUTLOOK_SEP_RE.search(text)
    if sep is not None:
        text = text[: sep.start()].rstrip()
    return text.strip()


def looks_like_closing_mail(body: str | None) -> bool:
    """True when the latest inbound reply is a courtesy close with no real ask.

    Quoted history is ignored so a prior "thank you" cannot hide a new ask,
    and a quoted ask cannot hide a real close. A standalone "Thank you!" line
    is a sign-off, not a close, unless that is the whole reply.
    """
    text = _newest_reply(body or "")
    if not text:
        return False
    remainder = _THANKS_LINE_RE.sub("", text).strip()
    if not remainder:
        return True
    # A real question mark means an open ask — "Thank you for any advice?"
    # must not collapse into a courtesy close via CLOSE_RE alone.
    if "?" in remainder:
        return False
    if _ASK_RE.search(remainder):
        return False
    return bool(_CLOSE_RE.search(remainder) or _CONDITIONAL_RE.search(remainder))
