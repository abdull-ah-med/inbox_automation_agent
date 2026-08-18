"""User-facing hybrid search request/response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

SEARCH_DEFAULT_LIMIT = 10
SEARCH_MAX_LIMIT = 25
SEARCH_QUERY_MAX_CHARS = 500
SEARCH_SNIPPET_MAX_CHARS = 240
SEARCH_MODE_KEYWORD = "keyword"
SEARCH_MODE_HYBRID = "hybrid"


class SearchColumnFilters(BaseModel):
    """SQL-side Discord filters applied in addition to FTS/vector ranking."""

    senders: tuple[str, ...] = ()
    contains: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    directions: tuple[str, ...] = ()

    def active(self) -> bool:
        return bool(self.senders or self.contains or self.subjects or self.directions)


class SearchRequest(BaseModel):
    """Documented search contract. The HTTP route uses equivalent query params."""

    model_config = ConfigDict(str_strip_whitespace=True)

    q: str = Field(min_length=1, max_length=SEARCH_QUERY_MAX_CHARS)
    mailbox: str | None = None
    limit: int = Field(default=SEARCH_DEFAULT_LIMIT, ge=1, le=SEARCH_MAX_LIMIT)


class SearchHit(BaseModel):
    thread_id: uuid.UUID
    mailbox: str
    conversation_id: str
    subject: str | None
    state: str
    urgency: str | None
    snippet: str
    score: float
    last_message_at: datetime | None = None


class SearchResponse(BaseModel):
    query: str
    mailbox: str | None
    hits: list[SearchHit]
