"""Haiku chat caller — grounded answer from retrieved threads.

LLM I/O only. No DB, Redis, Graph, or mail writes. Prompts come from chat_prompts.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from anthropic import APIError, AsyncAnthropic

from app.core.config import Settings
from app.core.exceptions import ChatError
from app.llm.chat_prompts import (
    CHAT_SYSTEM_PROMPT,
    turn_delimiter_block,
)
from app.llm.chat_tools import (
    CHAT_TOOLS,
    MAX_CHAT_TOOL_ITERATIONS,
    search_results_from_hits,
    search_results_from_overview,
)
from app.llm.pii_redact import scrub_text
from app.llm.prompts import salted_untrusted_tag, wrap_untrusted
from app.llm.smooth_deltas import smooth_deltas
from app.models.schemas.chat import ChatHistoryTurn
from app.models.schemas.search import SearchHit

logger = structlog.get_logger(__name__)

_TOOL_STATUS = {
    "search_mail": "Searching mail",
    "list_recent_threads": "Listing recent threads",
    "get_thread": "Opening thread",
    "get_overview": "Summarizing mailbox",
}

# Standard UUID plus the eval miss (12-4-4-4-12 hex groups).
_UUID_LIKE = re.compile(
    r"[0-9a-fA-F]{8,12}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    re.IGNORECASE,
)
_ID_CLAUSE = re.compile(
    r"\s*[\(\[]?(?:thread\s+)?id\s*:\s*"
    r"[0-9a-fA-F]{8,12}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"[\)\]]?",
    re.IGNORECASE,
)


def unknown_thread_ids_in_answer(
    answer: str,
    *,
    known_thread_ids: set[uuid.UUID],
) -> list[str]:
    """UUID-like tokens in the model text that were not retrieved this turn."""
    known = {str(thread_id).lower() for thread_id in known_thread_ids}
    found: list[str] = []
    seen: set[str] = set()
    for token in _UUID_LIKE.findall(answer or ""):
        lowered = token.lower()
        if lowered in known or lowered in seen:
            continue
        seen.add(lowered)
        found.append(token)
    return found


def sanitize_chat_answer(
    answer: str,
    *,
    known_thread_ids: set[uuid.UUID] | None = None,
) -> str:
    """Strip thread ids from answer text. Citations are attached by the app."""
    if known_thread_ids is not None:
        for token in unknown_thread_ids_in_answer(answer, known_thread_ids=known_thread_ids):
            logger.warning("chat.unknown_thread_id", thread_id=token)
    text = _ID_CLAUSE.sub("", answer)
    text = _UUID_LIKE.sub("", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\[\s*\]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _hit_block(hit: SearchHit) -> str:
    snippet = scrub_text(hit.snippet or "")
    subject = scrub_text(hit.subject or "") or "(no subject)"
    when = hit.last_message_at.isoformat() if hit.last_message_at else "(none)"
    return (
        f"mailbox: {hit.mailbox}\n"
        f"subject: {subject}\n"
        f"sender: {hit.sender or '(none)'}\n"
        f"state: {hit.state}\n"
        f"urgency: {hit.urgency or '(none)'}\n"
        f"last_message_at: {when}\n"
        f"snippet:\n{snippet}\n"
    )


@dataclass(frozen=True)
class ChatAgentResult:
    answer: str
    hits: list[SearchHit]
    grounded: bool = False
    tool_used_first: str | None = None
    tool_iterations: int = 0
    ttft_ms: int | None = None
    total_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


def _usage_fields(response: object) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
        }
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None),
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", None),
    }


def _text_from_content(content: object) -> str:
    parts: list[str] = []
    for block in content or []:  # type: ignore[union-attr]
        if getattr(block, "type", None) == "text" and getattr(block, "text", None):
            parts.append(str(block.text))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts).strip()


def _cited_search_results(history: Sequence[ChatHistoryTurn] | None) -> list[dict[str, Any]]:
    """Pack previously cited threads as Method-2 search_result blocks."""
    blocks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for turn in history or []:
        for cited in turn.citations:
            key = str(cited.thread_id)
            if key in seen:
                continue
            seen.add(key)
            title = cited.subject or "(no subject)"
            blocks.append(
                {
                    "type": "search_result",
                    "source": f"/threads/{cited.thread_id}",
                    "title": title,
                    "content": [
                        {
                            "type": "text",
                            "text": f"subject: {title}\nthread_id: {cited.thread_id}",
                        }
                    ],
                    "citations": {"enabled": True},
                }
            )
    return blocks


def _agent_user_content(
    question: str,
    history: Sequence[ChatHistoryTurn] | None,
    *,
    delimiter_tag: str | None = None,
) -> str | list[dict[str, Any]]:
    text = f"Reviewer question:\n{question.strip()}"
    results = _cited_search_results(history)
    blocks: list[dict[str, Any]] = []
    if delimiter_tag:
        blocks.append(turn_delimiter_block(delimiter_tag))
    if results:
        blocks.append({"type": "text", "text": text})
        blocks.extend(results)
        return blocks
    if blocks:
        blocks.append({"type": "text", "text": text})
        return blocks
    return text


def _agent_messages(
    *,
    question: str,
    history: Sequence[ChatHistoryTurn] | None,
    delimiter_tag: str | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": turn.role, "content": turn.content} for turn in history or []
    ]
    messages.append(
        {
            "role": "user",
            "content": _agent_user_content(
                question,
                history,
                delimiter_tag=delimiter_tag,
            ),
        }
    )
    return messages


def _tool_result_payload(
    *,
    tool_use_id: str,
    execution: Any,
    untrusted_tag: str | None = None,
) -> dict[str, Any]:
    error = getattr(execution, "error", None)
    hits = list(getattr(execution, "hits", []) or [])
    overview = getattr(execution, "overview", None)
    if error:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": str(error),
            "is_error": True,
        }
    tag = untrusted_tag or salted_untrusted_tag()
    if overview:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": _wrap_retrieved_blocks(
                search_results_from_overview(str(overview)),
                tag,
            ),
        }
    if not hits:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": "No matching threads.",
        }
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": _wrap_retrieved_blocks(search_results_from_hits(hits), tag),
    }


def _wrap_retrieved_blocks(blocks: list[dict[str, Any]], tag: str) -> list[dict[str, Any]]:
    wrapped: list[dict[str, Any]] = []
    for block in blocks:
        items = []
        for item in block.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                items.append(
                    {**item, "text": wrap_untrusted(tag, str(item.get("text") or ""))}
                )
            else:
                items.append(item)
        wrapped.append({**block, "content": items})
    return wrapped


async def _execute_tool_block(
    block: Any,
    execute_tool: Callable[[str, dict], Awaitable[Any]],
) -> tuple[Any, Any]:
    name = str(getattr(block, "name", "") or "")
    raw_input = getattr(block, "input", {}) or {}
    arguments = dict(raw_input) if isinstance(raw_input, dict) else {}
    execution = await execute_tool(name, arguments)
    return block, execution


CHAT_INPUT_CHAR_BUDGET = 100_000


def _content_chars(content: object) -> int:
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return len(str(content))
    total = 0
    for block in content:
        if not isinstance(block, dict):
            total += len(str(block))
            continue
        block_type = block.get("type")
        if block_type == "text":
            total += len(str(block.get("text") or ""))
        elif block_type == "search_result":
            total += len(str(block.get("title") or ""))
            total += _content_chars(block.get("content"))
        elif block_type == "tool_result":
            total += _content_chars(block.get("content"))
        else:
            total += len(str(block.get("text") or ""))
    return total


def _messages_chars(messages: Sequence[dict[str, Any]]) -> int:
    return sum(_content_chars(message.get("content")) for message in messages)


def _copy_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            blocks: list[Any] = []
            for block in content:
                if not isinstance(block, dict):
                    blocks.append(block)
                    continue
                cloned = dict(block)
                inner = cloned.get("content")
                if isinstance(inner, list):
                    cloned["content"] = [
                        dict(item) if isinstance(item, dict) else item for item in inner
                    ]
                blocks.append(cloned)
            copied.append({**message, "content": blocks})
        else:
            copied.append(dict(message))
    return copied


def _shrink_search_result_texts(
    messages: list[dict[str, Any]],
    *,
    budget_chars: int,
) -> None:
    while _messages_chars(messages) > budget_chars:
        longest: dict[str, Any] | None = None
        longest_len = 0
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "search_result":
                    continue
                for item in block.get("content") or []:
                    if not isinstance(item, dict):
                        continue
                    text = str(item.get("text") or "")
                    if len(text) > longest_len:
                        longest = item
                        longest_len = len(text)
        if longest is None or longest_len <= 1:
            return
        over = _messages_chars(messages) - budget_chars
        keep = max(1, longest_len - over)
        longest["text"] = str(longest.get("text") or "")[:keep]


def fit_chat_messages(
    messages: list[dict[str, Any]],
    *,
    budget_chars: int = CHAT_INPUT_CHAR_BUDGET,
    budget_tokens: int | None = None,
    token_counter: Callable[[list[dict[str, Any]]], int] | None = None,
) -> list[dict[str, Any]]:
    """Drop oldest turns, then shrink search_result text, to fit the budget."""
    fitted = _copy_messages(messages)
    use_tokens = token_counter is not None and budget_tokens is not None
    counted: int | None = None

    def over_budget() -> bool:
        nonlocal counted
        if use_tokens:
            try:
                counted = token_counter(fitted)  # type: ignore[misc]
            except Exception:
                logger.warning("chat_token_count_failed")
                return _messages_chars(fitted) > budget_chars
            return int(counted) > int(budget_tokens)
        return _messages_chars(fitted) > budget_chars

    while len(fitted) > 1 and over_budget():
        fitted.pop(0)
        counted = None
    if over_budget():
        _shrink_search_result_texts(fitted, budget_chars=budget_chars)
    return fitted


async def fit_chat_request(
    *,
    client: AsyncAnthropic,
    request: dict[str, Any],
    messages: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    """Prefer Anthropic count_tokens; fall back to the char heuristic."""
    if _messages_chars(messages) <= CHAT_INPUT_CHAR_BUDGET:
        return _copy_messages(messages)
    fitted = _copy_messages(messages)
    token_cache: dict[int, int] = {}

    async def tokens_for(current: list[dict[str, Any]]) -> int:
        key = _messages_chars(current)
        if key not in token_cache:
            result = await client.messages.count_tokens(
                model=request["model"],
                messages=current,
                system=request.get("system"),
                tools=request.get("tools"),
            )
            token_cache[key] = int(getattr(result, "input_tokens", 0) or 0)
        return token_cache[key]

    try:
        while len(fitted) > 1 and await tokens_for(fitted) > settings.chat_max_input_tokens:
            fitted.pop(0)
        if await tokens_for(fitted) > settings.chat_max_input_tokens:
            fitted = fit_chat_messages(fitted, budget_chars=CHAT_INPUT_CHAR_BUDGET)
        return fitted
    except Exception:
        logger.warning("chat_token_count_failed")
        return fit_chat_messages(messages)


async def iter_chat_agent(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    question: str,
    execute_tool: Callable[[str, dict], Awaitable[Any]],
    history: Sequence[ChatHistoryTurn] | None = None,
    initial_tool: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield status, optional token deltas, then a result with grounded hits."""
    if not settings.anthropic_api_key.strip():
        raise ChatError("ANTHROPIC_API_KEY is not set; cannot run chat")

    tag = salted_untrusted_tag()
    messages = _agent_messages(question=question, history=history, delimiter_tag=tag)
    first_choice: dict[str, Any] = (
        {"type": "tool", "name": initial_tool} if initial_tool else {"type": "any"}
    )
    request: dict[str, Any] = {
        "model": settings.chat_model,
        "max_tokens": settings.chat_max_tokens,
        "system": [
            {
                "type": "text",
                "text": CHAT_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        "tools": CHAT_TOOLS,
        "messages": messages,
        "tool_choice": first_choice,
    }
    hits_by_id: dict[Any, SearchHit] = {}
    grounded = False
    tools_used = False
    streamed_any = False
    last_error: Exception | None = None
    started = time.perf_counter()
    tool_used_first: str | None = initial_tool
    tool_iterations = 0
    ttft_ms: int | None = None
    usage: dict[str, int | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    }

    for attempt in range(2):
        try:
            for _iteration in range(MAX_CHAT_TOOL_ITERATIONS):
                request["messages"] = await fit_chat_request(
                    client=client,
                    request=request,
                    messages=messages,
                    settings=settings,
                )
                if grounded:
                    request["tool_choice"] = {"type": "none"}
                elif tools_used:
                    request["tool_choice"] = {"type": "auto"}
                else:
                    request["tool_choice"] = first_choice
                streamed: list[str] = []
                if grounded:
                    yield {"type": "retrieved", "hits": list(hits_by_id.values())}
                    async with client.messages.stream(**request) as stream:
                        async for text in smooth_deltas(stream.text_stream):
                            if not text:
                                continue
                            streamed.append(text)
                            streamed_any = True
                            if ttft_ms is None:
                                ttft_ms = int((time.perf_counter() - started) * 1000)
                            yield {"type": "delta", "text": text}
                        response = await stream.get_final_message()
                else:
                    response = await client.messages.create(**request)

                if getattr(response, "stop_reason", None) != "tool_use":
                    hits = list(hits_by_id.values())
                    raw = "".join(streamed) or _text_from_content(getattr(response, "content", []))
                    # Streamed deltas already reached the client; do not re-sanitize
                    # the public answer (would diverge from what was displayed).
                    # Non-stream path and cache writers still sanitize downstream.
                    if not grounded:
                        answer = ""
                    elif streamed_any:
                        answer = raw
                    else:
                        answer = sanitize_chat_answer(
                            raw,
                            known_thread_ids={hit.thread_id for hit in hits},
                        )
                    usage = _usage_fields(response)
                    input_tokens = usage["input_tokens"]
                    cache_read = usage["cache_read_tokens"]
                    cache_hit_ratio: float | None = None
                    if (
                        isinstance(input_tokens, int)
                        and input_tokens > 0
                        and isinstance(cache_read, int)
                    ):
                        cache_hit_ratio = round(cache_read / input_tokens, 3)
                    logger.info(
                        "chat_agent_complete",
                        model=settings.chat_model,
                        hit_count=len(hits),
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        cache_read_input_tokens=cache_read,
                        cache_creation_input_tokens=usage["cache_write_tokens"],
                        input_tokens=input_tokens,
                        cache_hit_ratio=cache_hit_ratio,
                    )
                    yield {
                        "type": "result",
                        "answer": answer,
                        "hits": hits,
                        "grounded": grounded,
                        "tool_used_first": tool_used_first,
                        "tool_iterations": tool_iterations,
                        "ttft_ms": ttft_ms,
                        "total_ms": int((time.perf_counter() - started) * 1000),
                        **usage,
                    }
                    return

                tool_use_blocks = [
                    block
                    for block in getattr(response, "content", [])
                    if getattr(block, "type", None) == "tool_use"
                ]
                if not tool_use_blocks:
                    break

                tool_iterations += 1
                if tool_used_first is None:
                    tool_used_first = getattr(tool_use_blocks[0], "name", None)
                usage = _usage_fields(response)
                messages.append({"role": "assistant", "content": response.content})
                announced_status: set[str] = set()
                for block in tool_use_blocks:
                    status = _TOOL_STATUS.get(str(getattr(block, "name", "") or ""), "")
                    if status and status not in announced_status:
                        announced_status.add(status)
                        yield {"type": "status", "text": status}
                # Sequential, not gather(): every tool call shares the one
                # request-scoped AsyncSession via the execute_tool closure,
                # and SQLAlchemy's AsyncSession is documented as unsafe for
                # concurrent use (IllegalStateChangeError / corrupted reads).
                # Claude issues 1-2 tool calls per turn in practice, so this
                # is cheap; revisit only if p95 latency traces show otherwise.
                executed = [
                    await _execute_tool_block(block, execute_tool)
                    for block in tool_use_blocks
                ]
                tool_results: list[dict[str, Any]] = []
                for _block, execution in executed:
                    status = str(getattr(execution, "status", "") or "")
                    if status and status not in announced_status:
                        announced_status.add(status)
                        yield {"type": "status", "text": status}
                    if not getattr(execution, "error", None):
                        for hit in getattr(execution, "hits", []) or []:
                            hits_by_id[hit.thread_id] = hit
                        if getattr(execution, "overview", None) or getattr(execution, "hits", None):
                            grounded = True
                    tools_used = True
                    tool_results.append(
                        _tool_result_payload(
                            tool_use_id=str(getattr(_block, "id", "") or ""),
                            execution=execution,
                            untrusted_tag=tag,
                        )
                    )
                messages.append({"role": "user", "content": tool_results})

            hits = list(hits_by_id.values())
            yield {
                "type": "result",
                "answer": "",
                "hits": hits,
                "grounded": grounded,
                "tool_used_first": tool_used_first,
                "tool_iterations": tool_iterations,
                "ttft_ms": ttft_ms,
                "total_ms": int((time.perf_counter() - started) * 1000),
                **usage,
            }
            return
        except APIError as exc:
            logger.warning(
                "chat_api_error",
                attempt=attempt,
                error=str(exc),
                status_code=getattr(exc, "status_code", None),
                streamed=streamed_any,
            )
            if streamed_any:
                raise ChatError("Claude chat failed") from exc
            last_error = exc

    raise ChatError("Claude chat failed") from last_error


async def run_chat_agent(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    question: str,
    execute_tool: Callable[[str, dict], Awaitable[Any]],
    history: Sequence[ChatHistoryTurn] | None = None,
    initial_tool: str | None = None,
) -> ChatAgentResult:
    """Run the retrieve-then-answer tool loop. Empty hits yield an empty answer."""
    answer = ""
    hits: list[SearchHit] = []
    grounded = False
    extra: dict[str, Any] = {}
    async for event in iter_chat_agent(
        client=client,
        settings=settings,
        question=question,
        execute_tool=execute_tool,
        history=history,
        initial_tool=initial_tool,
    ):
        if event.get("type") == "result":
            answer = str(event.get("answer") or "")
            hits = list(event.get("hits") or [])
            grounded = bool(event.get("grounded"))
            extra = {
                "tool_used_first": event.get("tool_used_first"),
                "tool_iterations": int(event.get("tool_iterations") or 0),
                "ttft_ms": event.get("ttft_ms"),
                "total_ms": event.get("total_ms"),
                "input_tokens": event.get("input_tokens"),
                "output_tokens": event.get("output_tokens"),
                "cache_read_tokens": event.get("cache_read_tokens"),
                "cache_write_tokens": event.get("cache_write_tokens"),
            }
    return ChatAgentResult(answer=answer, hits=hits, grounded=grounded, **extra)
