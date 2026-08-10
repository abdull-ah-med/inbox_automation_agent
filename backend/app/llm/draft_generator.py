"""Sonnet draft caller — reply/forward + urgency + teaching note.

LLM I/O only (plus optional injected skill-reference loader). No Slack/Graph.
Prompts come from ``prompts.py``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

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
MAX_TOOL_ITERATIONS = 4

READ_SKILL_REFERENCE_TOOL: dict[str, Any] = {
    "name": "read_skill_reference",
    "description": (
        "Load the full contents of a reference file bundled with an active "
        "skill. Only use when the current email requires details that are "
        "not in the SKILL.md body but are listed under Available reference "
        "files for that skill. Call once per file needed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "UUID of the skill that lists the reference file",
            },
            "path": {
                "type": "string",
                "description": (
                    "Exact relative_path shown in the skill's manifest, "
                    "e.g. references/client_rules.md"
                ),
            },
        },
        "required": ["skill_id", "path"],
        "additionalProperties": False,
    },
}

SkillReferenceLoader = Callable[[uuid.UUID, str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class DraftCallResult:
    draft: DraftSchema
    prompt_version: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


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
    urgency_hints: list[str] | None = None,
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

    if urgency_hints:
        urgency_lines = [f"- {item.strip()}" for item in urgency_hints if item.strip()]
        urgency_block = "\n".join(urgency_lines) if urgency_lines else "(none)"
    else:
        urgency_block = "(none)"

    instruction_block = ""
    if instruction and instruction.strip():
        instruction_block = (
            f"\nReviewer instruction (override the default approach):\n{instruction.strip()}\n"
        )

    return (
        f"Standing instructions (skills):\n{skills_block}\n\n"
        f"Previously flagged issues to avoid:\n{constraints_block}\n\n"
        f"Tone profile:\n{profile_block}\n\n"
        f"Tone references (similar past replies):\n{tone_block}\n\n"
        f"Past urgency corrections (prefer these signals when relevant):\n{urgency_block}\n\n"
        f"Mailbox: {email.mailbox}\n"
        f"Message ID: {email.message_id}\n"
        f"Conversation ID: {email.conversation_id}\n"
        f"Direction: {email.direction.value}\n"
        f"Sender: {email.sender}\n"
        f"To: {to_list}\n"
        f"CC: {cc_list}\n"
        f"Subject: {email.subject}\n"
        f"Received at: {email.received_at.isoformat()}\n"
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
        f"Cross-thread context:\n{cross_block}\n"
        f"{instruction_block}"
    )


def _system_blocks() -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": DRAFT_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _usage_tokens(response: Any) -> tuple[int | None, int | None]:
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
    output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
    return input_tokens, output_tokens


async def _parse_final(
    *,
    client: AsyncAnthropic,
    model: str,
    max_tokens: int,
    messages: list[dict[str, Any]],
) -> tuple[DraftSchema, int | None, int | None]:
    final_messages = [
        *messages,
        {
            "role": "user",
            "content": ("Return the final draft now as DraftSchema JSON only. Do not call tools."),
        },
    ]
    response = await client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=_system_blocks(),  # type: ignore[arg-type]
        messages=final_messages,  # type: ignore[arg-type]
        output_format=DraftSchema,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise DraftGenerationError("Sonnet draft returned empty parsed_output")
    return parsed, *_usage_tokens(response)


async def _run_tool_loop(
    *,
    client: AsyncAnthropic,
    model: str,
    max_tokens: int,
    user_content: str,
    reference_loader: SkillReferenceLoader | None,
) -> tuple[DraftSchema, int | None, int | None, list[dict[str, Any]], bool]:
    """Run optional tool iterations, then extract structured DraftSchema.

    Returns (draft, input_tokens, output_tokens, tool_calls, truncated).
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    tool_calls: list[dict[str, Any]] = []
    truncated = False
    input_tokens: int | None = None
    output_tokens: int | None = None

    if reference_loader is None:
        draft, in_tok, out_tok = await _parse_final(
            client=client,
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return draft, in_tok, out_tok, tool_calls, truncated

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_system_blocks(),  # type: ignore[arg-type]
            tools=[READ_SKILL_REFERENCE_TOOL],  # type: ignore[list-item]
            messages=messages,  # type: ignore[arg-type]
        )
        in_tok, out_tok = _usage_tokens(response)
        if in_tok is not None:
            input_tokens = (input_tokens or 0) + in_tok
        if out_tok is not None:
            output_tokens = (output_tokens or 0) + out_tok

        if response.stop_reason != "tool_use":
            break

        tool_use_blocks = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not tool_use_blocks:
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results: list[dict[str, Any]] = []
        for block in tool_use_blocks:
            raw_input = getattr(block, "input", {}) or {}
            skill_id_raw = raw_input.get("skill_id")
            path = raw_input.get("path")
            try:
                skill_id = uuid.UUID(str(skill_id_raw))
                if not isinstance(path, str) or not path.strip():
                    raise ValueError("path is required")
                loaded = await reference_loader(skill_id, path.strip())
                tool_calls.append(
                    {
                        "skill_id": str(skill_id),
                        "path": path.strip(),
                        "bytes": int(loaded.get("bytes") or 0),
                        "is_error": bool(loaded.get("is_error")),
                        "truncated": bool(loaded.get("truncated")),
                        "iteration": iteration + 1,
                    }
                )
                result_payload: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": getattr(block, "id", ""),
                    "content": str(loaded.get("content") or ""),
                }
                if loaded.get("is_error"):
                    result_payload["is_error"] = True
                tool_results.append(result_payload)
            except Exception as exc:
                tool_calls.append(
                    {
                        "skill_id": str(skill_id_raw),
                        "path": str(path),
                        "bytes": 0,
                        "is_error": True,
                        "iteration": iteration + 1,
                    }
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": getattr(block, "id", ""),
                        "content": f"Tool error: {type(exc).__name__}: {exc}",
                        "is_error": True,
                    }
                )
        messages.append({"role": "user", "content": tool_results})
    else:
        truncated = True
        logger.warning(
            "draft_tool_loop_truncated",
            max_iterations=MAX_TOOL_ITERATIONS,
            tool_calls=len(tool_calls),
        )

    draft, in_tok, out_tok = await _parse_final(
        client=client,
        model=model,
        max_tokens=max_tokens,
        messages=messages,
    )
    if in_tok is not None:
        input_tokens = (input_tokens or 0) + in_tok
    if out_tok is not None:
        output_tokens = (output_tokens or 0) + out_tok
    return draft, input_tokens, output_tokens, tool_calls, truncated


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
    urgency_hints: list[str] | None = None,
    instruction: str | None = None,
    reference_loader: SkillReferenceLoader | None = None,
) -> DraftCallResult:
    """Call Sonnet (tool loop when loader provided) and return a structured draft."""
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
        urgency_hints=urgency_hints,
        instruction=instruction,
        verbatim_tail=settings.thread_verbatim_tail,
        full_if_at_most=settings.thread_full_if_at_most,
    )
    started = time.perf_counter()
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            draft, input_tokens, output_tokens, tool_calls, truncated = await _run_tool_loop(
                client=client,
                model=model,
                max_tokens=max_tokens,
                user_content=user_content,
                reference_loader=reference_loader,
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
                tool_call_count=len(tool_calls),
                tool_loop_truncated=truncated,
            )
            return DraftCallResult(
                draft=draft,
                prompt_version=PROMPT_VERSION,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                tool_calls=tool_calls,
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
