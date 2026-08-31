"""Small text helpers shared across repositories."""

from __future__ import annotations


def truncate_display(text: str, max_len: int = 240) -> str:
    """Truncate for UI display, replacing the last char with an ellipsis when cut."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
