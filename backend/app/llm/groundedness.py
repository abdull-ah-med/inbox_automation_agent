"""Bounded Haiku check that chat answers stay inside citation text."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from anthropic import APIError, AsyncAnthropic

from app.core.config import Settings
from app.llm.chat_prompts import NO_MATCH_ANSWER, OUT_OF_SCOPE_ANSWER, WRITE_REFUSAL_ANSWER
from app.llm.prompts import GROUNDEDNESS_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

GroundednessVerdict = Literal["SUPPORTED", "UNSUPPORTED", "UNKNOWN"]

GROUNDEDNESS_TIMEOUT_SEC = 0.8
GROUNDEDNESS_MAX_TOKENS = 200
MIN_ANSWER_CHARS = 8

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Groundedness:
    verdict: GroundednessVerdict
    unsupported_spans: list[str]


def _supported() -> Groundedness:
    return Groundedness(verdict="SUPPORTED", unsupported_spans=[])


def _unknown() -> Groundedness:
    """H2: fail-closed. The verifier never actually ran (timeout, malformed
    response, or an Anthropic APIError), so we have no evidence either way —
    that must not be conflated with a real SUPPORTED verdict, or an
    UNSUPPORTED answer could silently enter the semantic cache and be served
    to every future asker for the rest of its TTL.
    """
    return Groundedness(verdict="UNKNOWN", unsupported_spans=[])


def _should_skip(answer: str) -> bool:
    text = (answer or "").strip()
    if len(text) < MIN_ANSWER_CHARS:
        return True
    canned = (WRITE_REFUSAL_ANSWER, NO_MATCH_ANSWER, OUT_OF_SCOPE_ANSWER)
    return any(text == item or text.startswith(item) for item in canned)


def _citation_text(citations: Sequence[Any]) -> str:
    parts: list[str] = []
    for item in citations:
        if isinstance(item, str):
            if item.strip():
                parts.append(item.strip())
            continue
        if isinstance(item, dict):
            text = item.get("text") or item.get("snippet")
            if text:
                parts.append(str(text).strip())
                continue
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("text"):
                    parts.append(str(block["text"]).strip())
            continue
        snippet = getattr(item, "snippet", None) or getattr(item, "text", None)
        if snippet:
            parts.append(str(snippet).strip())
    return "\n\n".join(part for part in parts if part)


def _parse_payload(raw: str) -> Groundedness:
    stripped = _JSON_FENCE.sub("", (raw or "").strip())
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        logger.warning("groundedness.parse_failed", raw=stripped[:200])
        return _unknown()
    verdict = str(payload.get("verdict") or "SUPPORTED").upper()
    spans = payload.get("unsupported_spans") or []
    cleaned = [str(span).strip() for span in spans if str(span).strip()]
    if verdict != "UNSUPPORTED":
        return _supported()
    return Groundedness(verdict="UNSUPPORTED", unsupported_spans=cleaned)


async def verify_grounded(
    answer: str,
    citations: Sequence[Any],
    *,
    client: AsyncAnthropic,
    settings: Settings,
) -> Groundedness:
    """Return SUPPORTED | UNSUPPORTED. Skips canned refusals, no-match, short answers."""
    if _should_skip(answer):
        return _supported()
    if not settings.anthropic_api_key.strip():
        return _supported()

    user = (
        f"Answer:\n{answer.strip()}\n\n"
        f"Citations:\n{_citation_text(citations) or '(none)'}"
    )

    async def _call() -> Groundedness:
        response = await client.messages.create(
            model=settings.classification_model,
            max_tokens=GROUNDEDNESS_MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": GROUNDEDNESS_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
        )
        chunks = [
            str(getattr(block, "text", "") or "")
            for block in response.content
            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        ]
        return _parse_payload("\n".join(chunks))

    try:
        return await asyncio.wait_for(_call(), timeout=GROUNDEDNESS_TIMEOUT_SEC)
    except TimeoutError:
        logger.warning("groundedness.timeout", timeout_ms=int(GROUNDEDNESS_TIMEOUT_SEC * 1000))
        return _unknown()
    except APIError as exc:
        logger.warning("groundedness.api_error", error=str(exc))
        return _unknown()
