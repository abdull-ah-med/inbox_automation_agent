"""Deterministic closing / courtesy-close detection (shared with frontend contract)."""

from __future__ import annotations

import re

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


def looks_like_closing_mail(body: str | None) -> bool:
    """True when the message is a courtesy close with no real ask."""
    text = (body or "").strip()
    if not text:
        return False
    if _ASK_RE.search(text):
        return False
    return bool(_CLOSE_RE.search(text) or _CONDITIONAL_RE.search(text))
