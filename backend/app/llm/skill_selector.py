"""Haiku skill selector — choose applicable skills from a candidate pool."""

from __future__ import annotations

import time
import uuid

import structlog
from anthropic import APIError, AsyncAnthropic
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import ClassificationError
from app.llm.prompts import PROMPT_VERSION, SKILL_SELECTION_SYSTEM_PROMPT
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.skill import SkillResponseSchema
from app.models.schemas.skill_candidate import SkillSelectionSchema

logger = structlog.get_logger(__name__)

SKILL_SELECT_MAX_TOKENS = 512
_BODY_PREVIEW_CHARS = 1200


def _build_user_content(
    *,
    subject: str,
    body_preview: str,
    triage: TriageResultSchema,
    candidates: list[SkillResponseSchema],
) -> str:
    lines = [
        f"- {skill.id} | {skill.name} | {(skill.description or '').strip() or '(no description)'}"
        for skill in candidates
    ]
    candidate_block = "\n".join(lines) if lines else "(none)"
    preview = body_preview.strip()
    if len(preview) > _BODY_PREVIEW_CHARS:
        preview = preview[: _BODY_PREVIEW_CHARS - 1].rstrip() + "…"
    return (
        f"Subject: {subject}\n"
        f"Body preview:\n{preview or '(empty)'}\n\n"
        f"Triage:\n"
        f"- has_action_items: {triage.has_action_items}\n"
        f"- action_items_summary: {triage.action_items_summary or '(none)'}\n"
        f"- needs_context: {triage.needs_context}\n"
        f"- routing_category: {triage.routing_category}\n\n"
        f"Candidate skills (id | name | description):\n{candidate_block}\n"
    )


async def _parse_once(
    *,
    client: AsyncAnthropic,
    model: str,
    max_tokens: int,
    user_content: str,
) -> SkillSelectionSchema:
    response = await client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": SKILL_SELECTION_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        output_format=SkillSelectionSchema,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise ClassificationError("Skill selection returned empty parsed_output")
    return parsed


async def select_skills(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    subject: str,
    body_preview: str,
    triage: TriageResultSchema,
    candidates: list[SkillResponseSchema],
) -> list[uuid.UUID]:
    """Return applicable skill IDs ⊆ candidate ids. Raises ClassificationError on fail."""
    if not candidates:
        return []
    if not settings.anthropic_api_key.strip():
        raise ClassificationError("ANTHROPIC_API_KEY is not set; cannot select skills")

    allowed = {skill.id for skill in candidates}
    model = settings.classification_model
    user_content = _build_user_content(
        subject=subject,
        body_preview=body_preview,
        triage=triage,
        candidates=candidates,
    )
    started = time.perf_counter()
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            parsed = await _parse_once(
                client=client,
                model=model,
                max_tokens=SKILL_SELECT_MAX_TOKENS,
                user_content=user_content,
            )
            selected = [sid for sid in parsed.applicable_skill_ids if sid in allowed]
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "skill_selection_complete",
                model=model,
                prompt_version=PROMPT_VERSION,
                candidate_count=len(candidates),
                selected_count=len(selected),
                latency_ms=latency_ms,
                attempt=attempt + 1,
            )
            return selected
        except (
            ValidationError,
            APIError,
            ClassificationError,
            TypeError,
            ValueError,
        ) as exc:
            last_error = exc
            logger.warning(
                "skill_selection_attempt_failed",
                model=model,
                attempt=attempt + 1,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )

    raise ClassificationError(
        f"Skill selection failed after retry: {type(last_error).__name__}: {last_error}"
    ) from last_error
