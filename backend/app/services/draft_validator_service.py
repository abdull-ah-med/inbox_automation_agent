"""Draft validator service — atom-rule check with at most one Sonnet retry.

When ``draft_validator_enabled`` is False (default) the validator is a no-op.

Flow:
  1. Haiku check — fast, cheap.  Returns violations list.
  2. If violations found → single Sonnet retry with violation context.
  3. Return the final (validated or retried) draft body.

At most one retry to bound cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from anthropic import AsyncAnthropic

from app.core.config import Settings
from app.llm.json_parse import parse_llm_json
from app.llm.prompts import ATOM_VALIDATE_SYSTEM_PROMPT, UNTRUSTED_VALIDATOR_TAG, wrap_untrusted

logger = structlog.get_logger(__name__)


@dataclass
class ValidationResult:
    """Outcome of draft validation."""

    body: str
    """The (possibly corrected) draft body."""

    violations_found: bool = False
    """Whether Haiku detected any violations on the first pass."""

    retried: bool = False
    """Whether a Sonnet retry was issued."""

    violations: list[dict] = field(default_factory=list)
    """Violations from the initial Haiku check."""


def _build_atom_rules_block(fix_atoms: list[dict]) -> str:
    """Format fix atoms as a numbered rule list for the validator prompt."""
    lines = []
    for i, atom in enumerate(fix_atoms, 1):
        atom_id = str(atom.get("id", ""))
        text = atom.get("atom_text") or atom.get("text", "")
        applies = atom.get("applies_when") or ""
        condition = f" (when: {applies})" if applies else ""
        lines.append(f"{i}. [{atom_id}] {text}{condition}")
    return "\n".join(lines)


def _parse_violations(raw: str) -> list[dict]:
    """Parse Haiku violation JSON; returns [] on any error."""
    obj = parse_llm_json(raw)
    if not isinstance(obj, dict):
        logger.warning("draft_validator_parse_failed")
        return []
    violations = obj.get("violations", [])
    if isinstance(violations, list):
        return [v for v in violations if isinstance(v, dict)]
    logger.warning("draft_validator_parse_failed")
    return []


async def validate_and_maybe_retry(
    client: AsyncAnthropic,
    settings: Settings,
    *,
    draft_body: str,
    fix_atoms: list[dict],
    email_text: str,
    thread_context: str = "",
) -> ValidationResult:
    """Check draft against fix atoms; retry once with Sonnet if violations found.

    When ``draft_validator_enabled`` is False the draft is returned unchanged.

    Args:
        client: Anthropic async client.
        settings: app settings; reads ``draft_validator_enabled`` and model names.
        draft_body: the draft body to validate.
        fix_atoms: list of FeedbackAtomSchema dicts with at least
                   ``id``, ``atom_text``/``text``, ``applies_when`` keys.
        email_text: the incoming email (for context in validation prompt).
        thread_context: optional thread summary for context.

    Returns:
        ValidationResult with the final body and metadata.
    """
    if not settings.draft_validator_enabled or not fix_atoms:
        return ValidationResult(body=draft_body)

    rules_block = _build_atom_rules_block(fix_atoms)
    user_content = (
        f"Email (context):\n{wrap_untrusted(UNTRUSTED_VALIDATOR_TAG, email_text[:2000])}\n\n"
        f"Draft reply to validate:\n{wrap_untrusted(UNTRUSTED_VALIDATOR_TAG, draft_body)}\n\n"
        f"Atom rules:\n{wrap_untrusted(UNTRUSTED_VALIDATOR_TAG, rules_block)}"
    )
    if thread_context:
        wrapped_context = wrap_untrusted(UNTRUSTED_VALIDATOR_TAG, thread_context[:1000])
        user_content = f"Thread context:\n{wrapped_context}\n\n" + user_content

    # --- Pass 1: Haiku check ---
    try:
        haiku_response = await client.messages.create(
            model=settings.classification_model,
            max_tokens=256,
            system=ATOM_VALIDATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception:
        logger.exception("draft_validator_haiku_failed")
        return ValidationResult(body=draft_body)

    raw_haiku = haiku_response.content[0].text if haiku_response.content else ""
    violations = _parse_violations(raw_haiku)

    if not violations:
        return ValidationResult(body=draft_body, violations_found=False, violations=[])

    logger.info(
        "draft_validator_violations_found",
        count=len(violations),
        violation_ids=[v.get("atom_id") for v in violations],
    )

    # --- Pass 2: Sonnet retry (at most once) ---
    violation_block = "\n".join(
        f"- [{v.get('atom_id', '?')}]: {v.get('reason', '')}" for v in violations
    )
    retry_user_content = (
        f"{user_content}\n\n"
        f"The draft above violates these atom rules:\n{violation_block}\n\n"
        f"Rewrite the draft body to fix ALL violations. "
        f"Return only the plain-text draft body — no JSON, no preamble."
    )

    try:
        sonnet_response = await client.messages.create(
            model=settings.draft_model,
            max_tokens=1024,
            system=(
                "You are a copy-editor. Rewrite the draft to satisfy the listed "
                "rules. Plain text only — no JSON, no markdown fences."
            ),
            messages=[{"role": "user", "content": retry_user_content}],
        )
        retried_body = (
            sonnet_response.content[0].text.strip() if sonnet_response.content else draft_body
        )
    except Exception:
        logger.exception("draft_validator_sonnet_retry_failed")
        retried_body = draft_body

    return ValidationResult(
        body=retried_body,
        violations_found=True,
        retried=True,
        violations=violations,
    )
