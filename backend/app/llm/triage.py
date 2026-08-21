"""Haiku triage caller — spam / action-items / needs_context.

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
from app.llm.context_pack import pack_same_thread
from app.llm.email_clean import effective_body_text
from app.llm.pii_redact import scrub_email_for_llm, scrub_thread_for_llm
from app.llm.prompts import (
    PROMPT_VERSION,
    TRIAGE_SYSTEM_PROMPT,
    UNTRUSTED_EMAIL_TAG,
    wrap_untrusted,
)
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TriageCallResult:
    triage: TriageResultSchema
    prompt_version: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


def _build_user_content(
    email: EmailMessageSchema,
    thread_context: ThreadContextSchema,
    *,
    verbatim_tail: int = 2,
    full_if_at_most: int = 5,
    mailbox_owner: str | None = None,
) -> str:
    thread_block = pack_same_thread(
        thread_context,
        current_message_id=email.message_id,
        verbatim_tail=verbatim_tail,
        full_if_at_most=full_if_at_most,
    )
    to_list = ", ".join(email.to_recipients) if email.to_recipients else "(none)"
    cc_list = ", ".join(email.cc_recipients) if email.cc_recipients else "(none)"
    body = effective_body_text(
        body_clean=email.body_clean,
        body_text=email.body_text,
        body_content_type=email.body_content_type,
    )
    outlook_location = ""
    if (email.graph_folder or "").strip().lower() == "junkemail":
        outlook_location = "Outlook location: Junk Email\n"

    owner_line = ""
    if mailbox_owner and mailbox_owner.strip():
        name = mailbox_owner.strip()
        owner_line = (
            f"Mailbox owner: {name} (personal inbox — mail here is for {name} specifically)\n"
        )

    email_block = (
        f"Mailbox: {email.mailbox}\n"
        f"{owner_line}"
        f"Message ID: {email.message_id}\n"
        f"Conversation ID: {email.conversation_id}\n"
        f"Direction: {email.direction.value}\n"
        f"Sender: {email.sender}\n"
        f"To: {to_list}\n"
        f"CC: {cc_list}\n"
        f"{outlook_location}"
        f"Subject: {email.subject}\n"
        f"Received at: {email.received_at.isoformat()}\n"
        f"Body:\n{body}\n\n"
        f"Thread context ({len(thread_context.messages)} messages, oldest first):\n"
        f"{thread_block}\n"
    )
    return wrap_untrusted(UNTRUSTED_EMAIL_TAG, email_block)


async def _parse_once(
    *,
    client: AsyncAnthropic,
    model: str,
    max_tokens: int,
    user_content: str,
) -> tuple[TriageResultSchema, int | None, int | None]:
    response = await client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": TRIAGE_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        output_format=TriageResultSchema,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise TriageError("Haiku triage returned empty parsed_output")
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
    output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
    return parsed, input_tokens, output_tokens


async def triage_email(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    email: EmailMessageSchema,
    thread_context: ThreadContextSchema,
) -> TriageCallResult:
    """Call Haiku once (retry once on parse/API failure) and return structured triage."""
    if not settings.anthropic_api_key.strip():
        raise TriageError("ANTHROPIC_API_KEY is not set; cannot run Haiku triage")

    model = settings.classification_model
    max_tokens = settings.triage_max_tokens
    # Scrub copies only — originals in Postgres stay intact for human review.
    user_content = _build_user_content(
        scrub_email_for_llm(email),
        scrub_thread_for_llm(thread_context),
        verbatim_tail=settings.thread_verbatim_tail,
        full_if_at_most=settings.thread_full_if_at_most,
        mailbox_owner=settings.owner_for_mailbox(email.mailbox),
    )
    started = time.perf_counter()
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            triage, input_tokens, output_tokens = await _parse_once(
                client=client,
                model=model,
                max_tokens=max_tokens,
                user_content=user_content,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "triage_complete",
                model=model,
                prompt_version=PROMPT_VERSION,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                is_spam=triage.is_spam,
                has_action_items=triage.has_action_items,
                needs_context=triage.needs_context,
                attempt=attempt + 1,
            )
            return TriageCallResult(
                triage=triage,
                prompt_version=PROMPT_VERSION,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
            )
        except (ValidationError, APIError, TriageError, TypeError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "triage_attempt_failed",
                model=model,
                prompt_version=PROMPT_VERSION,
                attempt=attempt + 1,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )

    raise TriageError(
        f"Haiku triage failed after retry: {type(last_error).__name__}: {last_error}"
    ) from last_error
