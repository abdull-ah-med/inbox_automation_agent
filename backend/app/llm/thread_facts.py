"""Haiku ADD-only fact extract for a thread. LLM I/O only."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

import structlog
from anthropic import AsyncAnthropic

from app.core.config import Settings
from app.llm.email_clean import effective_body_text
from app.llm.json_parse import parse_llm_json
from app.llm.prompts import (
    THREAD_FACTS_SYSTEM_PROMPT,
    UNTRUSTED_THREAD_FACTS_TAG,
    wrap_untrusted,
)
from app.repositories.message_repo import MessageSchema

logger = structlog.get_logger(__name__)

THREAD_FACTS_MAX_TOKENS = 1024


def parse_thread_facts(
    raw: str,
    *,
    allowed_message_ids: set[uuid.UUID],
) -> list[dict]:
    """Parse Haiku JSON; drop empty text and source ids we did not pass in."""
    obj = parse_llm_json(raw)
    if not isinstance(obj, dict):
        return []
    facts_raw = obj.get("facts")
    if not isinstance(facts_raw, list):
        return []
    valid: list[dict] = []
    allowed = {str(mid) for mid in allowed_message_ids}
    for item in facts_raw:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        raw_id = item.get("source_message_id")
        if raw_id is None:
            continue
        try:
            source_id = uuid.UUID(str(raw_id))
        except (ValueError, TypeError):
            continue
        if str(source_id) not in allowed:
            continue
        valid.append({"text": text, "source_message_id": source_id})
    return valid


def _pack_messages(messages: Sequence[MessageSchema]) -> str:
    parts: list[str] = []
    for message in messages:
        body = effective_body_text(
            body_clean=message.body_clean,
            body_text=message.body_text,
            body_content_type=message.body_content_type,
        )
        parts.append(
            f"message_id={message.id}\n"
            f"direction={message.direction}\n"
            f"from_name={getattr(message, 'sender_name', None) or ''}\n"
            f"from_email={message.sender}\n"
            f"received_at={message.received_at.isoformat()}\n"
            f"body:\n{body}"
        )
    return "\n\n".join(parts)


async def extract_facts(
    client: AsyncAnthropic,
    settings: Settings,
    messages: Sequence[MessageSchema],
    *,
    allowed_message_ids: Iterable[uuid.UUID] | None = None,
) -> list[dict]:
    """Call Haiku and return validated ADD-only facts. Empty list on failure."""
    if not messages:
        return []
    allowed = (
        set(allowed_message_ids) if allowed_message_ids is not None else {m.id for m in messages}
    )
    user_content = wrap_untrusted(UNTRUSTED_THREAD_FACTS_TAG, _pack_messages(messages))

    async def _once() -> list[dict]:
        response = await client.messages.create(
            model=settings.classification_model,
            max_tokens=THREAD_FACTS_MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": THREAD_FACTS_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )
        if not response.content:
            return []
        raw_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw_text += getattr(block, "text", "") or ""
        return parse_thread_facts(raw_text, allowed_message_ids=allowed)

    try:
        parsed = await _once()
    except Exception:
        logger.exception("thread_facts_api_call_failed")
        return []
    if parsed:
        return parsed
    try:
        return await _once()
    except Exception:
        logger.exception("thread_facts_api_retry_failed")
        return []
