"""Fence-tolerant JSON parse for Haiku/Sonnet structured replies.

Models often wrap JSON in markdown fences despite being told not to.
Strip a leading/trailing ```json (or bare ```) fence, then json.loads.
Returns None on any parse failure so callers stay best-effort.
"""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$",
    re.DOTALL | re.IGNORECASE,
)


def parse_llm_json(raw: str) -> object | None:
    """Return the parsed JSON object, or None when *raw* is not valid JSON."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    match = _FENCE_RE.match(text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
