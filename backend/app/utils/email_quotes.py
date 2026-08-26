"""Compatibility re-export — canonical module is ``app.core.email_quotes``."""

from app.core.email_quotes import (
    EMBED_CLEAN_VERSION,
    QUOTE_PATTERN_SOURCES,
    QuoteStrippedBody,
    find_quote_boundary,
    split_quoted_history,
    strip_quoted_reply,
)

__all__ = [
    "EMBED_CLEAN_VERSION",
    "QUOTE_PATTERN_SOURCES",
    "QuoteStrippedBody",
    "find_quote_boundary",
    "split_quoted_history",
    "strip_quoted_reply",
]
