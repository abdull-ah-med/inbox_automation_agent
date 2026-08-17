"""Chat ask request/response contracts for the grounded NL assistant."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas.search import SEARCH_DEFAULT_LIMIT, SEARCH_MAX_LIMIT, SEARCH_QUERY_MAX_CHARS

CHAT_DEFAULT_LIMIT = SEARCH_DEFAULT_LIMIT
CHAT_MAX_LIMIT = SEARCH_MAX_LIMIT
CHAT_MESSAGE_MAX_CHARS = SEARCH_QUERY_MAX_CHARS


class ChatAskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=CHAT_MESSAGE_MAX_CHARS)
    mailbox: str | None = None
    limit: int | None = Field(default=None, ge=1, le=CHAT_MAX_LIMIT)


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
