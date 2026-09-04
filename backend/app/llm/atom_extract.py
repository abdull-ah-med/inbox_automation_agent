"""SLIFT-shaped atom extraction via Haiku.

Calls the classification model (Haiku) with a structured prompt and parses the
JSON response into a list of atom dicts with keys:
  text, role, applies_when, suggested_scope
"""

from __future__ import annotations

import structlog
from anthropic import AsyncAnthropic

from app.core.config import Settings
from app.llm.json_parse import parse_llm_json
from app.llm.prompts import (
    ATOM_EXTRACT_SYSTEM_PROMPT,
    UNTRUSTED_ATOM_EXTRACT_TAG,
    wrap_untrusted,
)

logger = structlog.get_logger(__name__)

MAX_EXTRACTED_ATOMS = 8
_VALID_ROLES = frozenset({"Fix", "Spec", "Null"})
_VALID_SCOPES = frozenset(
    {
        "thread",
        "sender_address",
        "sender_domain",
        "mailbox+routing_category",
        "mailbox",
    }
)


def _parse_atoms(raw: str) -> list[dict]:
    """Parse and validate the raw JSON from the model.

    Returns an empty list on any parse/schema failure (best-effort).
    """
    obj = parse_llm_json(raw)
    if not isinstance(obj, dict):
        logger.warning("atom_extract_json_parse_failed")
        return []

    atoms_raw = obj.get("atoms")
    if not isinstance(atoms_raw, list):
        logger.warning("atom_extract_no_atoms_key", keys=list(obj.keys()))
        return []

    valid: list[dict] = []
    for item in atoms_raw:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        role = (item.get("role") or "").strip()
        applies_when = item.get("applies_when") or None
        suggested_scope = (item.get("suggested_scope") or "").strip()

        if not text:
            continue
        if role not in _VALID_ROLES:
            logger.warning("atom_extract_invalid_role", role=role)
            role = "Null"
        if suggested_scope not in _VALID_SCOPES:
            # Silently demote to mailbox rather than rejecting
            suggested_scope = "mailbox"

        valid.append(
            {
                "text": text,
                "role": role,
                "applies_when": applies_when,
                "suggested_scope": suggested_scope,
            }
        )
        if len(valid) >= MAX_EXTRACTED_ATOMS:
            break
    return valid


async def extract_atoms(
    client: AsyncAnthropic,
    settings: Settings,
    *,
    feedback_text: str,
    email_text_for_context: str | None = None,
    draft_body: str | None = None,
) -> list[dict]:
    """Call Haiku and return a list of parsed atom dicts.

    Returns an empty list on any error (best-effort; caller must not raise).

    Args:
        client: Anthropic async client (mocked at boundary in tests).
        settings: app settings (reads classification_model).
        feedback_text: the reviewer note / rejection feedback to decompose.
        email_text_for_context: optional original email body for context.
    """
    if not feedback_text.strip():
        return []

    parts = [wrap_untrusted(UNTRUSTED_ATOM_EXTRACT_TAG, f"Reviewer note:\n{feedback_text.strip()}")]
    if email_text_for_context and email_text_for_context.strip():
        parts.append(
            wrap_untrusted(
                UNTRUSTED_ATOM_EXTRACT_TAG,
                f"Original email (context only):\n{email_text_for_context.strip()}",
            )
        )
    if draft_body and draft_body.strip():
        parts.append(
            wrap_untrusted(
                UNTRUSTED_ATOM_EXTRACT_TAG,
                f"DraftAssistant draft (context only):\n{draft_body.strip()}",
            )
        )
    user_content = "\n".join(parts)

    try:
        response = await client.messages.create(
            model=settings.classification_model,
            max_tokens=512,
            system=ATOM_EXTRACT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception:
        logger.exception("atom_extract_api_call_failed")
        return []

    if not response.content:
        return []

    raw_text = response.content[0].text if hasattr(response.content[0], "text") else ""
    return _parse_atoms(raw_text)
