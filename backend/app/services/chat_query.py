"""Resolve the search string and cited-thread follow-ups for a chat turn.

Follow-ups like ``tell me more`` have no content tokens. Reuse the last user
ask that does, so retrieval stays on the same threads. Ordinals (``the first
one``) resolve against previously cited thread ids.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

from app.models.schemas.chat import ChatHistoryTurn
from app.services.search_service import build_fts_query, build_prefix_tsquery

_ORDINAL_INDEX = {
    "first": 0,
    "1st": 0,
    "second": 1,
    "2nd": 1,
    "third": 2,
    "3rd": 2,
}
_ORDINAL_RE = re.compile(
    r"\b(first|1st|second|2nd|third|3rd|last)\b",
    re.IGNORECASE,
)
_DEICTIC_RE = re.compile(
    r"\b(that thread|this thread|that one|this one)\b",
    re.IGNORECASE,
)


def has_search_tokens(text: str) -> bool:
    return bool(build_prefix_tsquery(build_fts_query(text)))


def retrieval_message(message: str, history: Sequence[ChatHistoryTurn]) -> str:
    """Return the query to retrieve: this turn, or the last contentful user ask."""
    if has_search_tokens(message):
        return message
    for turn in reversed(history):
        if turn.role != "user":
            continue
        if has_search_tokens(turn.content):
            return turn.content
    return message


def _cited_thread_ids(history: Sequence[ChatHistoryTurn]) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for turn in history:
        for cited in turn.citations:
            if cited.thread_id in seen:
                continue
            seen.add(cited.thread_id)
            ids.append(cited.thread_id)
    return ids


def resolve_cited_thread(
    message: str,
    history: Sequence[ChatHistoryTurn],
) -> uuid.UUID | None:
    """Map 'the first one' / 'that thread' onto previously cited thread ids."""
    ids = _cited_thread_ids(history)
    if not ids:
        return None
    if _DEICTIC_RE.search(message or ""):
        return ids[-1]
    match = _ORDINAL_RE.search(message or "")
    if match is None:
        return None
    token = match.group(1).lower()
    if token == "last":
        return ids[-1]
    index = _ORDINAL_INDEX.get(token)
    if index is None or index >= len(ids):
        return None
    return ids[index]
