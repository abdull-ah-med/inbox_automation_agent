"""Read-only InboxAssistant tools and Anthropic search_result packing.

No DB. Tool execution lives in ``app.services.chat_tools``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.llm.pii_redact import scrub_text
from app.models.schemas.search import SearchHit

MAX_CHAT_TOOL_ITERATIONS = 5
GET_THREAD_CONTENT_BLOCKS = 8


@dataclass(frozen=True)
class ChatToolExecution:
    hits: list[SearchHit]
    status: str
    error: str | None = None
    overview: str | None = None


CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_mail",
        "description": (
            "Search ingested mail by topic, person, keyword, or phrase. "
            "Call this before answering questions about specific threads, "
            "senders, invoices, screens, or other mailbox content. "
            "Read-only; cannot send or change mail."
        ),
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language or keyword search query.",
                },
                "mailbox": {
                    "type": "string",
                    "description": "Optional mailbox email or local-part to scope the search.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_recent_threads",
        "description": (
            "List the latest threads in scope. Call this for overviews: "
            "what to focus on, what's new, hello, or latest on a mailbox. "
            "Read-only; cannot send or change mail."
        ),
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "mailbox": {
                    "type": "string",
                    "description": "Optional mailbox email or local-part to scope the list.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_thread",
        "description": (
            "Open one thread by id and read its messages. Call this for "
            "follow-ups like 'the first one', 'that thread', or when a "
            "snippet is not enough. Use thread_id values from tool results "
            "or previously cited threads. Read-only; cannot send or change mail."
        ),
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "UUID of the thread to open.",
                },
                "page": {
                    "type": "integer",
                    "description": (
                        "0 = most recent messages plus summary; 1+ = older windows with no overlap."
                    ),
                },
            },
            "required": ["thread_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_overview",
        "description": (
            "Mailbox-level counts: how many threads, how many awaiting "
            "review, urgency mix, and stale volume. Call this for "
            "'how many threads are waiting' or 'what's the queue like'. "
            "Does not return individual threads. Read-only."
        ),
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "mailbox": {
                    "type": "string",
                    "description": "Optional mailbox email or local-part to scope the counts.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        "cache_control": {"type": "ephemeral"},
    },
]


def search_results_from_hits(hits: list[SearchHit]) -> list[dict[str, Any]]:
    """Pack hits as Anthropic search_result blocks with citations enabled."""
    blocks: list[dict[str, Any]] = []
    for hit in hits:
        title = scrub_text(hit.subject or "") or "(no subject)"
        packed = {
            "type": "search_result",
            "source": f"/threads/{hit.thread_id}",
            "title": title,
            "content": _content_blocks(hit, title=title),
            "citations": {"enabled": True},
        }
        blocks.append(packed)
    return blocks


def search_results_from_overview(overview: str) -> list[dict[str, Any]]:
    """Pack mailbox counts as a citable search_result (no thread id)."""
    text = (overview or "").strip() or "No mailbox data."
    return [
        {
            "type": "search_result",
            "source": "/dashboard",
            "title": "Mailbox overview",
            "content": [{"type": "text", "text": text}],
            "citations": {"enabled": True},
        }
    ]


def _content_blocks(hit: SearchHit, *, title: str) -> list[dict[str, str]]:
    """Split snippet vs recency so Claude can cite a passage, not the whole hit."""
    snippet = scrub_text(hit.snippet or "")
    parts = [part.strip() for part in snippet.split("\n\n") if part.strip()]
    content: list[dict[str, str]] = [{"type": "text", "text": "source_kind: mail_snippet"}]
    content.extend({"type": "text", "text": part} for part in parts[:GET_THREAD_CONTENT_BLOCKS])
    if not content:
        content.append({"type": "text", "text": title})
    when = hit.last_message_at.isoformat() if hit.last_message_at else "(none)"
    urgency = hit.urgency or "none"
    content.append(
        {
            "type": "text",
            "text": (
                f"state: {hit.state}\n"
                f"urgency: {urgency}\n"
                f"sender: {hit.sender or '(none)'}\n"
                f"mailbox: {hit.mailbox}\n"
                f"last_message_at: {when}"
            ),
        }
    )
    return content
