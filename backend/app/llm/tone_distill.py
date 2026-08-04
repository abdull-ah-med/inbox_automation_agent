"""Haiku tone distill — approved replies → ToneProfileSchema."""

from __future__ import annotations

import time

import structlog
from anthropic import APIError, AsyncAnthropic
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import ClassificationError
from app.llm.prompts import PROMPT_VERSION, TONE_DISTILL_SYSTEM_PROMPT
from app.models.schemas.tone_profile import ToneProfileSchema

logger = structlog.get_logger(__name__)

TONE_DISTILL_MAX_TOKENS = 1024


def _build_user_content(reply_bodies: list[str]) -> str:
    blocks: list[str] = []
    for idx, body in enumerate(reply_bodies, start=1):
        trimmed = body.strip()
        if not trimmed:
            continue
        if len(trimmed) > 2000:
            trimmed = trimmed[:1999].rstrip() + "…"
        blocks.append(f"--- Reply {idx} ---\n{trimmed}")
    joined = "\n\n".join(blocks) if blocks else "(none)"
    return f"Distill a ToneProfileSchema from these approved reply bodies.\n\n{joined}\n"


async def _parse_once(
    *,
    client: AsyncAnthropic,
    model: str,
    max_tokens: int,
    user_content: str,
) -> ToneProfileSchema:
    response = await client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": TONE_DISTILL_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        output_format=ToneProfileSchema,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise ClassificationError("Tone distill returned empty parsed_output")
    return parsed


async def distill_tone_profile(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    reply_bodies: list[str],
) -> ToneProfileSchema:
    """Call Haiku once (retry once) to distill a tone profile."""
    if not settings.anthropic_api_key.strip():
        raise ClassificationError("ANTHROPIC_API_KEY is not set; cannot distill tone")
    if not reply_bodies:
        raise ClassificationError("tone distill requires at least one reply body")

    model = settings.classification_model
    user_content = _build_user_content(reply_bodies)
    started = time.perf_counter()
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            profile = await _parse_once(
                client=client,
                model=model,
                max_tokens=TONE_DISTILL_MAX_TOKENS,
                user_content=user_content,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "tone_distill_complete",
                model=model,
                prompt_version=PROMPT_VERSION,
                sample_count=len(reply_bodies),
                latency_ms=latency_ms,
                attempt=attempt + 1,
            )
            return profile
        except (
            ValidationError,
            APIError,
            ClassificationError,
            TypeError,
            ValueError,
        ) as exc:
            last_error = exc
            logger.warning(
                "tone_distill_attempt_failed",
                model=model,
                attempt=attempt + 1,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )

    raise ClassificationError(
        f"Tone distill failed after retry: {type(last_error).__name__}: {last_error}"
    ) from last_error
