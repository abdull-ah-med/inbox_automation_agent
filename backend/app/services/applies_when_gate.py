"""Applies-when gate — filter atom/note candidates by email context.

When ``applies_when_gate_enabled`` is False the gate is a no-op (all True).
When enabled, calls Haiku once with the email text and the candidate conditions;
Haiku returns a boolean mask indicating which candidates apply.
"""

from __future__ import annotations

import structlog
from anthropic import AsyncAnthropic

from app.core.config import Settings
from app.llm.json_parse import parse_llm_json
from app.llm.prompts import UNTRUSTED_GATE_TAG, wrap_untrusted

logger = structlog.get_logger(__name__)

_GATE_SYSTEM_PROMPT = """\
You receive an incoming email and a list of feedback atom conditions.
For each condition, decide whether it applies to this email.
Return JSON only (no preamble, no markdown fences):
{"applies": [true, false, ...]}
The list must have the same length and order as the input conditions.
Be conservative: return true only when the condition clearly applies.
"""


async def gate_atoms_and_notes(
    client: AsyncAnthropic,
    settings: Settings,
    email_text: str,
    candidates: list[str | None],
) -> list[bool]:
    """Return a boolean mask indicating which candidates apply to *email_text*.

    When ``applies_when_gate_enabled`` is False (default), returns all True
    without any API call.

    Args:
        client: Anthropic async client.
        settings: app settings; reads ``applies_when_gate_enabled``.
        email_text: the incoming email body (already scrubbed by caller).
        candidates: list of ``applies_when`` strings (or None for unconditional).
                    None entries are always True.

    Returns:
        List of bool, same length and order as *candidates*.
    """
    if not candidates:
        return []

    # Gate off — all candidates pass through
    if not settings.applies_when_gate_enabled:
        return [True] * len(candidates)

    # Unconditional candidates (applies_when is None) are always True
    conditional_indices = [i for i, c in enumerate(candidates) if c is not None]
    if not conditional_indices:
        return [True] * len(candidates)

    conditions = [candidates[i] for i in conditional_indices]
    wrapped_email = wrap_untrusted(UNTRUSTED_GATE_TAG, email_text.strip()[:4000])
    wrapped_conditions = "\n".join(
        f"{n + 1}. {wrap_untrusted(UNTRUSTED_GATE_TAG, str(cond))}"
        for n, cond in enumerate(conditions)
    )
    user_content = (
        f"Email:\n{wrapped_email}\n\nConditions (one per line, numbered):\n{wrapped_conditions}"
    )

    try:
        response = await client.messages.create(
            model=settings.classification_model,
            max_tokens=256,
            system=_GATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception:
        logger.exception("applies_when_gate_api_failed")
        return [c is None for c in candidates]

    raw = response.content[0].text if response.content else ""
    obj = parse_llm_json(raw)
    try:
        if not isinstance(obj, dict):
            raise ValueError("not an object")
        applies_list = obj.get("applies", [])
        if not isinstance(applies_list, list) or len(applies_list) != len(conditions):
            raise ValueError("bad response length")
        gate_results = [bool(v) for v in applies_list]
    except Exception:
        logger.warning("applies_when_gate_parse_failed")
        gate_results = [False] * len(conditions)

    result = [True] * len(candidates)
    for list_pos, orig_idx in enumerate(conditional_indices):
        result[orig_idx] = gate_results[list_pos]

    return result
