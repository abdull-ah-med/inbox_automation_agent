"""Deterministic NL → retrieval plan for InboxAssistant.

Does not replace the tool loop. Classifies the ask so the first Claude call
can force the matching read-only tool (or refuse off-topic without an LLM).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.models.schemas.chat import ChatHistoryTurn
from app.services.chat_query import resolve_cited_thread
from app.services.nl_mailbox_scope import extract_nl_mailbox_scope
from app.services.search_service import build_fts_query, build_prefix_tsquery

_AGGREGATION_RE = re.compile(
    r"\b(how many|how much|count of|what'?s the queue|queue like)\b",
    re.IGNORECASE,
)
_OFF_TOPIC_RE = re.compile(
    r"\b(weather|poem|poems|joke|jokes|recipe|recipes|sports|movie|movies|"
    r"song|songs|capital of)\b",
    re.IGNORECASE,
)
_MAIL_WORK_RE = re.compile(
    r"\b(mail|email|inbox|mailbox|thread|draft|invoice|billing|dispute|"
    r"sender|samplelab|screen|urgency|queue)\b",
    re.IGNORECASE,
)


class ChatIntent(StrEnum):
    OVERVIEW = "overview"
    SEARCH = "search"
    FOLLOW_UP = "follow_up"
    AGGREGATION = "aggregation"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class ChatIntentPlan:
    intent: ChatIntent
    tool_name: str | None = None
    mailbox: str | None = None
    thread_id: uuid.UUID | None = None


def classify_chat_intent(
    message: str,
    *,
    history: Sequence[ChatHistoryTurn],
    mailbox_emails: list[str],
) -> ChatIntentPlan:
    """Map a reviewer ask to a retrieval tool, or refuse if it is not about mail."""
    nl = extract_nl_mailbox_scope(message, mailbox_emails)
    mailbox = nl.mailboxes[0] if nl.mailboxes else None
    remainder = nl.free_text
    cited = resolve_cited_thread(message, history)
    if cited is not None:
        return ChatIntentPlan(
            intent=ChatIntent.FOLLOW_UP,
            tool_name="get_thread",
            mailbox=mailbox,
            thread_id=cited,
        )
    if _OFF_TOPIC_RE.search(message) and not _MAIL_WORK_RE.search(message) and not mailbox:
        return ChatIntentPlan(intent=ChatIntent.OUT_OF_SCOPE)
    if _AGGREGATION_RE.search(message):
        return ChatIntentPlan(
            intent=ChatIntent.AGGREGATION,
            tool_name="get_overview",
            mailbox=mailbox,
        )
    leftover_tokens = build_prefix_tsquery(build_fts_query(remainder))
    if leftover_tokens:
        return ChatIntentPlan(
            intent=ChatIntent.SEARCH,
            tool_name="search_mail",
            mailbox=mailbox,
        )
    return ChatIntentPlan(
        intent=ChatIntent.OVERVIEW,
        tool_name="list_recent_threads",
        mailbox=mailbox,
    )
