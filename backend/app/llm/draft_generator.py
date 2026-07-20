"""Sonnet draft caller — reply/forward + urgency + teaching note.

LLM I/O only. No DB, Redis, Graph, or Slack. Prompts come from ``prompts.py``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog
from anthropic import APIError, AsyncAnthropic
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import DraftGenerationError
from app.llm.pii_redact import scrub_email_for_llm, scrub_thread_for_llm
from app.llm.prompts import DRAFT_SYSTEM_PROMPT, PROMPT_VERSION
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema

logger = structlog.get_logger(__name__)

DRAFT_MAX_TOKENS = 800


@dataclass(frozen=True, slots=True)
class DraftCallResult:
    draft: DraftSchema
    prompt_version: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


def _build_user_content(
    email: EmailMessageSchema,
    thread_context: ThreadContextSchema,
    triage: TriageResultSchema,
    *,
    cross_thread_context: str | None = None,
    tone_references: list[str] | None = None,
) -> str:
    thread_lines: list[str] = []
    for msg in thread_context.messages:
        preview = msg.body_preview or msg.body_text[:240]
        thread_lines.append(
            f"- [{msg.direction.value}] from={msg.sender} "
            f"at={msg.received_at.isoformat()} preview={preview!r}"
        )
    thread_block = "\n".join(thread_lines) if thread_lines else "(no prior messages)"
    to_list = ", ".join(email.to_recipients) if email.to_recipients else "(none)"
    cc_list = ", ".join(email.cc_recipients) if email.cc_recipients else "(none)"

    action_summary = triage.action_items_summary or "(none)"
    spam_reason = triage.spam_reason or "(none)"
    context_reason = triage.context_reason or "(none)"

    cross_block = cross_thread_context.strip() if cross_thread_context else "(none)"
    if tone_references:
        tone_block = "\n".join(f"- {ref}" for ref in tone_references if ref.strip())
        if not tone_block:
            tone_block = "(none)"
    else:
        tone_block = "(none)"

    return (
        f"Mailbox: {email.mailbox}\n"
        f"Message ID: {email.message_id}\n"
        f"Conversation ID: {email.conversation_id}\n"
        f"Direction: {email.direction.value}\n"
        f"Sender: {email.sender}\n"
        f"To: {to_list}\n"
        f"CC: {cc_list}\n"
        f"Subject: {email.subject}\n"
        f"Received at: {email.received_at.isoformat()}\n"
        f"Body:\n{email.body_text}\n\n"
        f"Thread context ({len(thread_context.messages)} messages, oldest first):\n"
        f"{thread_block}\n\n"
        f"Triage result:\n"
        f"- is_spam: {triage.is_spam}\n"
        f"- spam_reason: {spam_reason}\n"
        f"- has_action_items: {triage.has_action_items}\n"
        f"- action_items_summary: {action_summary}\n"
        f"- needs_context: {triage.needs_context}\n"
        f"- context_reason: {context_reason}\n\n"
        f"Cross-thread context:\n{cross_block}\n\n"
        f"Tone references (similar past replies):\n{tone_block}\n"
    )


async def _parse_once(
    *,
    client: AsyncAnthropic,
    model: str,
    max_tokens: int,
    user_content: str,
) -> tuple[DraftSchema, int | None, int | None]:
    response = await client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": DRAFT_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        output_format=DraftSchema,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise DraftGenerationError("Sonnet draft returned empty parsed_output")
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
    output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
    return parsed, input_tokens, output_tokens


async def generate_draft(
    email: EmailMessageSchema,
    thread_context: ThreadContextSchema,
    triage: TriageResultSchema,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    cross_thread_context: str | None = None,
    tone_references: list[str] | None = None,
) -> DraftCallResult:
    """Call Sonnet once (retry once on parse/API failure) and return a structured draft."""
    if not settings.anthropic_api_key.strip():
        raise DraftGenerationError("ANTHROPIC_API_KEY is not set; cannot run Sonnet draft")

    model = settings.draft_model
    max_tokens = DRAFT_MAX_TOKENS
    # Scrub copies only — originals in Postgres stay intact for human review.
    user_content = _build_user_content(
        scrub_email_for_llm(email),
        scrub_thread_for_llm(thread_context),
        triage,
        cross_thread_context=cross_thread_context,
        tone_references=tone_references,
    )
    started = time.perf_counter()
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            draft, input_tokens, output_tokens = await _parse_once(
                client=client,
                model=model,
                max_tokens=max_tokens,
                user_content=user_content,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "draft_complete",
                model=model,
                prompt_version=PROMPT_VERSION,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                urgency=draft.urgency,
                attempt=attempt + 1,
            )
            return DraftCallResult(
                draft=draft,
                prompt_version=PROMPT_VERSION,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
            )
        except (
            ValidationError,
            APIError,
            DraftGenerationError,
            TypeError,
            ValueError,
        ) as exc:
            last_error = exc
            logger.warning(
                "draft_attempt_failed",
                model=model,
                prompt_version=PROMPT_VERSION,
                attempt=attempt + 1,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )

    raise DraftGenerationError(
        f"Sonnet draft failed after retry: {type(last_error).__name__}: {last_error}"
    ) from last_error
