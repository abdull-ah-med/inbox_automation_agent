"""Resolve the search string for a chat turn.

Follow-ups like ``tell me more`` have no content tokens. Reuse the last user
ask that does, so retrieval stays on the same threads.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.schemas.chat import ChatHistoryTurn
from app.services.search_service import build_fts_query, build_prefix_tsquery


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
