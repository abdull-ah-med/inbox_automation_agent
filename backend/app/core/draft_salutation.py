"""Deterministic rewrite of a draft's opening greeting — no LLM."""

from __future__ import annotations

import re

_OPENING_GREETING_RE = re.compile(
    r"^(?P<greeting>Hi|Hello|Hey)(?:\s+(?P<name>[^\n,]+))?,[ \t]*",
    re.IGNORECASE,
)


def format_opening_salutation(salute_name: str) -> str:
    """Render the canonical opening line (without trailing newline)."""
    name = (salute_name or "").strip()
    if not name:
        return "Hi,"
    return f"Hi {name},"


def rewrite_opening_salutation(body: str, salute_name: str) -> str:
    """Replace or prepend the opening ``Hi …,`` line in ``body``.

    Always normalizes to ``Hi {name},`` / ``Hi,`` so a local-part guess like
    ``Hi Samplecontact,`` becomes ``Hi Kelvin,`` without calling the model.
    """
    text = body if body is not None else ""
    opening = format_opening_salutation(salute_name)
    if not text.strip():
        return f"{opening}\n"

    match = _OPENING_GREETING_RE.match(text)
    if match is not None:
        return opening + text[match.end() :]

    # No greeting line — prepend with a blank line before the existing body.
    rest = text.lstrip("\n")
    return f"{opening}\n\n{rest}"
