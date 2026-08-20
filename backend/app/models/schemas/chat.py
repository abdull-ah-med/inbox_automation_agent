"""Chat ask request/response contracts for the grounded NL assistant."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas.search import SEARCH_DEFAULT_LIMIT, SEARCH_MAX_LIMIT, SEARCH_QUERY_MAX_CHARS

CHAT_DEFAULT_LIMIT = SEARCH_DEFAULT_LIMIT
CHAT_MAX_LIMIT = SEARCH_MAX_LIMIT
CHAT_MESSAGE_MAX_CHARS = SEARCH_QUERY_MAX_CHARS
CHAT_HISTORY_CONTENT_MAX_CHARS = 8_000
CHAT_HISTORY_MAX_TURNS = 20


class ChatCitedThread(BaseModel):
    thread_id: uuid.UUID
    subject: str | None = None


class ChatHistoryTurn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=CHAT_HISTORY_CONTENT_MAX_CHARS)
    citations: list[ChatCitedThread] = Field(default_factory=list)


class ChatAskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=CHAT_MESSAGE_MAX_CHARS)
    mailbox: str | None = None
    limit: int | None = Field(default=None, ge=1, le=CHAT_MAX_LIMIT)
    history: list[ChatHistoryTurn] = Field(default_factory=list, max_length=CHAT_HISTORY_MAX_TURNS)
    bypass_cache: bool = False


class ChatCitation(BaseModel):
    thread_id: uuid.UUID
    mailbox: str
    subject: str | None
    state: str
    urgency: str | None
    snippet: str | None = None
    url_path: str


class ChatAskResponse(BaseModel):
    answer: str
    citations: list[ChatCitation]
    retrieval_count: int
    mailbox: str | None
    refused_write: bool
    cached: bool = False
    cache_similarity: float | None = None
    grounded_verifier: Literal["SUPPORTED", "UNSUPPORTED", "SKIPPED"] = "SKIPPED"
