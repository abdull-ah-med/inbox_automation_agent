"""Haiku per-message summary caller — structured JSON for prompt packing.

LLM I/O only. No DB, Redis, Graph, or Slack. Prompts come from ``prompts.py``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog
from anthropic import APIError, AsyncAnthropic
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import TriageError
from app.llm.email_clean import effective_body_text
from app.llm.pii_redact import scrub_text
from app.llm.prompts import MESSAGE_SUMMARY_SYSTEM_PROMPT, PROMPT_VERSION
from app.models.schemas.email import EmailMessageSchema
from app.models.schemas.summary import MessageSummarySchema

logger = structlog.get_logger(__name__)

SUMMARY_MAX_TOKENS = 400
_MIN_BODY_CHARS = 20


@dataclass(frozen=True, slots=True)
class SummaryCallResult:
    summary: MessageSummarySchema
    prompt_version: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


def _build_user_content(email: EmailMessageSchema) -> str:
    body = effective_body_text(
        body_clean=email.body_clean,
        body_text=email.body_text,
        body_content_type=email.body_content_type,
    )
    body = scrub_text(body)
    to_list = ", ".join(email.to_recipients) if email.to_recipients else "(none)"
    cc_list = ", ".join(email.cc_recipients) if email.cc_recipients else "(none)"
    return (
        f"Mailbox: {email.mailbox}\n"
        f"Direction: {email.direction.value}\n"
        f"Sender: {email.sender}\n"
        f"To: {to_list}\n"
        f"CC: {cc_list}\n"
        f"Subject: {scrub_text(email.subject)}\n"
        f"Received at: {email.received_at.isoformat()}\n"
        f"Body:\n{body}\n"
    )


def should_summarize_body(email: EmailMessageSchema) -> bool:
    body = effective_body_text(
        body_clean=email.body_clean,
        body_text=email.body_text,
        body_content_type=email.body_content_type,
    )
    return len(body.strip()) >= _MIN_BODY_CHARS


async def _parse_once(
    *,
    client: AsyncAnthropic,
    model: str,
    max_tokens: int,
    user_content: str,
) -> tuple[MessageSummarySchema, int | None, int | None]:
    response = await client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": MESSAGE_SUMMARY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        output_format=MessageSummarySchema,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise TriageError("Haiku message summary returned empty parsed_output")
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
    output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
    return parsed, input_tokens, output_tokens


async def summarize_message(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    email: EmailMessageSchema,
) -> SummaryCallResult:
    """Call Haiku once (retry once on parse/API failure) for a message summary."""
    if not settings.anthropic_api_key.strip():
        raise TriageError("ANTHROPIC_API_KEY is not set; cannot run Haiku summary")
    if not should_summarize_body(email):
        raise TriageError("message body too short to summarize")

    model = settings.classification_model
    max_tokens = SUMMARY_MAX_TOKENS
    user_content = _build_user_content(email)
    started = time.perf_counter()
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            summary, input_tokens, output_tokens = await _parse_once(
                client=client,
                model=model,
                max_tokens=max_tokens,
                user_content=user_content,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "message_summary_complete",
                model=model,
                prompt_version=PROMPT_VERSION,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                attempt=attempt + 1,
                message_id=email.message_id,
            )
            return SummaryCallResult(
                summary=summary,
                prompt_version=PROMPT_VERSION,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
            )
        except (ValidationError, APIError, TriageError, TypeError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "message_summary_attempt_failed",
                model=model,
                prompt_version=PROMPT_VERSION,
                attempt=attempt + 1,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
                message_id=email.message_id,
            )

    raise TriageError(
        f"Haiku message summary failed after retry: {type(last_error).__name__}: {last_error}"
    ) from last_error
