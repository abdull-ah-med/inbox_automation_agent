"""Thread prompt packing — verbatim tail + older summaries.

Used by triage and draft generators so both see the same context shape.
"""

from __future__ import annotations

from app.llm.email_clean import effective_body_text
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema


def anchor_first_and_newest(
    messages: list[EmailMessageSchema],
    *,
    cap: int,
) -> list[EmailMessageSchema]:
    """Keep the thread-starter plus the newest (cap-1); never silently drop message 1."""
    if len(messages) <= cap:
        return messages
    if cap <= 1:
        return messages[-cap:]
    return [messages[0], *messages[-(cap - 1) :]]


def _body_for_prompt(msg: EmailMessageSchema) -> str:
    return effective_body_text(
        body_clean=msg.body_clean,
        body_text=msg.body_text,
        body_content_type=msg.body_content_type,
    )


def _format_summary_line(msg: EmailMessageSchema) -> str:
    one_line = (msg.summary_one_line or "").strip()
    if one_line:
        preview = one_line
    else:
        body = _body_for_prompt(msg)
        preview = body[:400] if body else (msg.body_preview or "")
    return (
        f"- [{msg.direction.value}] from={msg.sender} "
        f"at={msg.received_at.isoformat()} subject={msg.subject!r} "
        f"summary={preview!r}"
    )


def _format_full_line(msg: EmailMessageSchema) -> str:
    body = _body_for_prompt(msg)
    return (
        f"- [{msg.direction.value}] from={msg.sender} "
        f"at={msg.received_at.isoformat()} subject={msg.subject!r}\n"
        f"  body:\n{body}"
    )


def pack_same_thread(
    thread_context: ThreadContextSchema,
    *,
    current_message_id: str | None = None,
    verbatim_tail: int = 2,
    full_if_at_most: int = 5,
) -> str:
    """Render same-thread context for triage/draft user turns.

    - If total messages <= ``full_if_at_most``: all cleaned bodies.
    - Else: messages after the current + last ``verbatim_tail`` peers are full;
      older peers are summary lines.
    Messages are expected oldest-first.
    """
    messages = list(thread_context.messages)
    if not messages:
        return "(no prior messages)"

    if len(messages) <= full_if_at_most:
        return "\n".join(_format_full_line(m) for m in messages)

    # Identify current message index (last matching id, else last message).
    current_idx = len(messages) - 1
    if current_message_id is not None:
        for i, msg in enumerate(messages):
            if msg.message_id == current_message_id:
                current_idx = i

    # Verbatim window: current + previous ``verbatim_tail`` messages.
    verbatim_start = max(0, current_idx - verbatim_tail)
    packed_lines: list[str] = []
    for i, msg in enumerate(messages):
        if i >= verbatim_start:
            packed_lines.append(_format_full_line(msg))
        else:
            packed_lines.append(_format_summary_line(msg))
    return "\n".join(packed_lines)


def pack_cross_thread(
    cross_thread_context: CrossThreadContextSchema | str | None,
    *,
    verbatim_newest: int = 1,
) -> str:
    """Render related-thread context: summaries for older, full for newest."""
    if cross_thread_context is None:
        return "(none)"
    if isinstance(cross_thread_context, str):
        return cross_thread_context.strip() or "(none)"

    messages = list(cross_thread_context.thread_messages)
    if not messages:
        body = "(no messages in matched thread)"
    else:
        if len(messages) <= verbatim_newest:
            lines = [_format_full_line(m) for m in messages]
        else:
            older = messages[:-verbatim_newest]
            newest = messages[-verbatim_newest:]
            lines = [_format_summary_line(m) for m in older]
            lines.extend(_format_full_line(m) for m in newest)
        body = "\n".join(lines)

    score = cross_thread_context.similarity_score
    return (
        f"Related prior conversation "
        f"(conversation_id={cross_thread_context.matched_conversation_id}, "
        f"score={score:.4f}, "
        f"{len(messages)} messages, oldest first):\n"
        f"{body}"
    )
