"""Thread prompt packing — verbatim tail + older summaries.

Used by triage and draft generators so both see the same context shape.
"""

from __future__ import annotations

from app.llm.email_clean import effective_body_text
from app.llm.prompts import (
    UNTRUSTED_PRIOR_SENDS_TAG,
    UNTRUSTED_THREAD_FACTS_TAG,
    UNTRUSTED_THREAD_PINS_TAG,
    wrap_untrusted,
)
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
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


def _pack_working_memory(
    messages: list[EmailMessageSchema],
    *,
    verbatim_tail: int,
    user_notes: str,
    facts: list[dict],
    prior_sends: list[dict],
    org_identity: str,
) -> str:
    """Lost-in-the-Middle pack: pins/facts first, original ask, her sends, latest last."""
    parts: list[str] = []
    identity = org_identity.strip()
    if identity:
        parts.append(f"Org identity:\n{identity}")
    notes = user_notes.strip()
    if notes:
        parts.append(wrap_untrusted(UNTRUSTED_THREAD_PINS_TAG, notes))
    if facts:
        fact_lines = []
        for fact in facts:
            text = str(fact.get("text") or "").strip()
            source = fact.get("source_message_id")
            if not text:
                continue
            if source is None:
                fact_lines.append(f"- {text}")
            else:
                fact_lines.append(f"- [source={source}] {text}")
        if fact_lines:
            parts.append(
                wrap_untrusted(
                    UNTRUSTED_THREAD_FACTS_TAG,
                    "Thread facts:\n" + "\n".join(fact_lines),
                )
            )
    if prior_sends:
        send_blocks = []
        for send in prior_sends:
            body = str(send.get("body") or "").strip()
            if body:
                send_blocks.append(body)
        if send_blocks:
            parts.append(wrap_untrusted(UNTRUSTED_PRIOR_SENDS_TAG, "\n\n".join(send_blocks)))

    included: set[str] = set()
    selected: list[EmailMessageSchema] = []
    inbound = [m for m in messages if m.direction == EmailDirectionEnum.INBOUND]
    outbound = [m for m in messages if m.direction == EmailDirectionEnum.OUTBOUND]
    if inbound:
        selected.append(inbound[0])
        included.add(inbound[0].message_id)
    for msg in outbound:
        if msg.message_id not in included:
            selected.append(msg)
            included.add(msg.message_id)
    tail_n = max(0, verbatim_tail)
    for msg in messages[-tail_n:] if tail_n else []:
        if msg.message_id not in included:
            selected.append(msg)
            included.add(msg.message_id)
    if selected:
        parts.append("Thread messages:\n" + "\n".join(_format_full_line(m) for m in selected))
    return "\n\n".join(parts) if parts else "(no prior messages)"


def pack_same_thread(
    thread_context: ThreadContextSchema,
    *,
    current_message_id: str | None = None,
    verbatim_tail: int = 2,
    full_if_at_most: int = 5,
    user_notes: str = "",
    facts: list[dict] | None = None,
    prior_sends: list[dict] | None = None,
    org_identity: str = "",
) -> str:
    """Render same-thread context for triage/draft user turns.

    - If pins or facts exist: working-memory pack (pins, facts, first inbound,
      her outbounds, verbatim tail). One-liners are not used.
    - Else if total messages <= ``full_if_at_most``: all cleaned bodies.
    - Else: messages after the current + last ``verbatim_tail`` peers are full;
      older peers are summary lines.
    Messages are expected oldest-first.
    """
    messages = list(thread_context.messages)
    fact_list = list(facts or [])
    send_list = list(prior_sends or [])
    if (user_notes or "").strip() or fact_list:
        return _pack_working_memory(
            messages,
            verbatim_tail=verbatim_tail,
            user_notes=user_notes,
            facts=fact_list,
            prior_sends=send_list,
            org_identity=org_identity,
        )
    if not messages:
        return "(no prior messages)"

    if len(messages) <= full_if_at_most:
        packed = "\n".join(_format_full_line(m) for m in messages)
    else:
        current_idx = len(messages) - 1
        if current_message_id is not None:
            for i, msg in enumerate(messages):
                if msg.message_id == current_message_id:
                    current_idx = i
        verbatim_start = max(0, current_idx - verbatim_tail)
        packed_lines: list[str] = []
        for i, msg in enumerate(messages):
            if i >= verbatim_start:
                packed_lines.append(_format_full_line(msg))
            else:
                packed_lines.append(_format_summary_line(msg))
        packed = "\n".join(packed_lines)
    identity = org_identity.strip()
    if identity:
        return f"Org identity:\n{identity}\n\n{packed}"
    return packed


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
