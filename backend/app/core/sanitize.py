"""Sanitize untrusted user text before search, chat, and echoed query strings."""

from __future__ import annotations

import re

_HTML_TAG_RE = re.compile(r"<[^>]*>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FTS_OPERATOR_RE = re.compile(r"[&|!():*'\"]")


def sanitize_user_text(text: str) -> str:
    """Strip tags, NULs, control chars, and FTS operators; collapse whitespace."""
    cleaned = _HTML_TAG_RE.sub(" ", text or "")
    cleaned = _CONTROL_RE.sub("", cleaned)
    cleaned = _FTS_OPERATOR_RE.sub(" ", cleaned)
    return " ".join(cleaned.split())
