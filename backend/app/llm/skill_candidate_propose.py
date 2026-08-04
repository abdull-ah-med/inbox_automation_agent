"""Haiku skill candidate propose — reject notes → standing skill draft."""

from __future__ import annotations

import time

import structlog
from anthropic import APIError, AsyncAnthropic
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import ClassificationError
from app.llm.prompts import PROMPT_VERSION, SKILL_CANDIDATE_SYSTEM_PROMPT
from app.models.schemas.skill_candidate import SkillCandidateProposeSchema

logger = structlog.get_logger(__name__)

SKILL_CANDIDATE_MAX_TOKENS = 768


def _build_user_content(
    *,
    mailbox: str,
    routing_category: str,
    reason_code: str,
    notes: list[str],
) -> str:
    note_block = "\n".join(f"- {n.strip()}" for n in notes if n.strip()) or "(none)"
    return (
        f"Mailbox: {mailbox}\n"
        f"Routing category: {routing_category}\n"
        f"Reject reason code: {reason_code}\n\n"
        f"Recurring rejection notes:\n{note_block}\n"
    )


async def propose_skill_from_notes(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    mailbox: str,
    routing_category: str,
    reason_code: str,
    notes: list[str],
) -> SkillCandidateProposeSchema:
    """Propose one skill from rejection notes. Raises ClassificationError on fail."""
    if not settings.anthropic_api_key.strip():
        raise ClassificationError("ANTHROPIC_API_KEY is not set; cannot propose skill")
    if not notes:
        raise ClassificationError("skill propose requires notes")

    model = settings.classification_model
    user_content = _build_user_content(
        mailbox=mailbox,
        routing_category=routing_category,
        reason_code=reason_code,
        notes=notes,
    )
    started = time.perf_counter()
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            response = await client.messages.parse(
                model=model,
                max_tokens=SKILL_CANDIDATE_MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": SKILL_CANDIDATE_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
                output_format=SkillCandidateProposeSchema,
            )
            parsed = response.parsed_output
            if parsed is None:
                raise ClassificationError("Skill propose returned empty parsed_output")
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "skill_candidate_propose_complete",
                model=model,
                prompt_version=PROMPT_VERSION,
                note_count=len(notes),
                latency_ms=latency_ms,
                attempt=attempt + 1,
            )
            return parsed
        except (
            ValidationError,
            APIError,
            ClassificationError,
            TypeError,
            ValueError,
        ) as exc:
            last_error = exc
            logger.warning(
                "skill_candidate_propose_attempt_failed",
                model=model,
                attempt=attempt + 1,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )

    raise ClassificationError(
        f"Skill propose failed after retry: {type(last_error).__name__}: {last_error}"
    ) from last_error
