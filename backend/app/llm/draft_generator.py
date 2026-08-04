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
from app.llm.context_pack import pack_cross_thread, pack_same_thread
from app.llm.email_clean import effective_body_text
from app.llm.pii_redact import scrub_email_for_llm, scrub_thread_for_llm
from app.llm.prompts import DRAFT_SYSTEM_PROMPT, PROMPT_VERSION
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema

logger = structlog.get_logger(__name__)

# Reply + teaching note + suggested_actions JSON; 800 truncated mid-string in practice.
DRAFT_MAX_TOKENS = 2048


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
    cross_thread_context: CrossThreadContextSchema | str | None = None,
    tone_references: list[str] | None = None,
    tone_profile: str | None = None,
    skills: list[str] | None = None,
    negative_constraints: list[str] | None = None,
    instruction: str | None = None,
    verbatim_tail: int = 2,
    full_if_at_most: int = 5,
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

    action_summary = triage.action_items_summary or "(none)"
    spam_reason = triage.spam_reason or "(none)"
    context_reason = triage.context_reason or "(none)"

    cross_block = pack_cross_thread(cross_thread_context)
    if tone_references:
        tone_block = "\n".join(f"- {ref}" for ref in tone_references if ref.strip())
        if not tone_block:
            tone_block = "(none)"
    else:
        tone_block = "(none)"

    profile_block = tone_profile.strip() if tone_profile and tone_profile.strip() else "(none)"

    if skills:
        skill_lines = [skill.strip() for skill in skills if skill.strip()]
        skills_block = "\n\n".join(skill_lines) if skill_lines else "(none)"
    else:
        skills_block = "(none)"

    if negative_constraints:
        constraint_lines = [f"- {item.strip()}" for item in negative_constraints if item.strip()]
        constraints_block = "\n".join(constraint_lines) if constraint_lines else "(none)"
    else:
        constraints_block = "(none)"

    instruction_block = ""
    if instruction and instruction.strip():
        instruction_block = (
            f"\nReviewer instruction (override the default approach):\n{instruction.strip()}\n"
        )

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
        f"Standing instructions (skills):\n{skills_block}\n\n"
        f"Body:\n{body}\n\n"
        f"Thread context ({len(thread_context.messages)} messages, oldest first):\n"
        f"{thread_block}\n\n"
        f"Triage result:\n"
        f"- is_spam: {triage.is_spam}\n"
        f"- spam_reason: {spam_reason}\n"
        f"- has_action_items: {triage.has_action_items}\n"
        f"- action_items_summary: {action_summary}\n"
        f"- needs_context: {triage.needs_context}\n"
        f"- context_reason: {context_reason}\n"
        f"- routing_category: {triage.routing_category}\n\n"
        f"Cross-thread context:\n{cross_block}\n\n"
        f"Tone profile:\n{profile_block}\n\n"
        f"Tone references (similar past replies):\n{tone_block}\n\n"
        f"Previously flagged issues to avoid:\n{constraints_block}\n"
        f"{instruction_block}"
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


def _scrub_cross_thread(
    cross_thread_context: CrossThreadContextSchema | str | None,
) -> CrossThreadContextSchema | str | None:
    if cross_thread_context is None or isinstance(cross_thread_context, str):
        return cross_thread_context
    scrubbed_messages = [scrub_email_for_llm(msg) for msg in cross_thread_context.thread_messages]
    return cross_thread_context.model_copy(update={"thread_messages": scrubbed_messages})


async def generate_draft(
    email: EmailMessageSchema,
    thread_context: ThreadContextSchema,
    triage: TriageResultSchema,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    cross_thread_context: CrossThreadContextSchema | str | None = None,
    tone_references: list[str] | None = None,
    tone_profile: str | None = None,
    skills: list[str] | None = None,
    negative_constraints: list[str] | None = None,
    instruction: str | None = None,
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
        cross_thread_context=_scrub_cross_thread(cross_thread_context),
        tone_references=tone_references,
        tone_profile=tone_profile,
        skills=skills,
        negative_constraints=negative_constraints,
        instruction=instruction,
        verbatim_tail=settings.thread_verbatim_tail,
        full_if_at_most=settings.thread_full_if_at_most,
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
