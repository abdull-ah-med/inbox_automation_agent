"""Haiku caller for thread-level summaries. LLM I/O only."""

from __future__ import annotations

from collections.abc import Sequence

from anthropic import AsyncAnthropic

from app.core.config import Settings
from app.core.exceptions import ChatError
from app.llm.email_clean import effective_body_text
from app.llm.pii_redact import scrub_text
from app.llm.prompts import THREAD_SUMMARY_SYSTEM_PROMPT
from app.repositories.message_repo import MessageSchema
from app.utils.email_quotes import strip_quoted_reply

THREAD_SUMMARY_MAX_TOKENS = 200


def _pack_messages(messages: Sequence[MessageSchema]) -> str:
    parts: list[str] = []
    for message in messages:
        body = effective_body_text(
            body_clean=message.body_clean,
            body_text=message.body_text,
            body_content_type=message.body_content_type,
        )
        body = strip_quoted_reply(body).text
        body = scrub_text(body)
        when = message.received_at.isoformat() if message.received_at else ""
        parts.append(f"From: {scrub_text(message.sender)} ({when})\n{body}")
    return "\n\n".join(parts)


async def summarize_thread(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    messages: Sequence[MessageSchema],
) -> str:
    if not settings.anthropic_api_key.strip():
        raise ChatError("ANTHROPIC_API_KEY is not set; cannot summarize thread")
    packed = _pack_messages(messages)
    response = await client.messages.create(
        model=settings.classification_model,
        max_tokens=THREAD_SUMMARY_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": THREAD_SUMMARY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": packed or "(empty thread)"}],
    )
    chunks = [
        str(getattr(block, "text", "") or "")
        for block in response.content
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ]
    text = "\n".join(chunks).strip()
    if not text:
        raise ChatError("Haiku thread summary returned empty text")
    return text
