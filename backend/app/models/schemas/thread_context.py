"""API schemas for per-thread working memory."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ThreadContextFactView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    body: str
    source_message_id: uuid.UUID | None = None
    created_at: datetime | None = None
    source_received_at: datetime | None = None


class ThreadContextView(BaseModel):
    version: int
    user_notes: str
    facts: list[ThreadContextFactView] = Field(default_factory=list)
    updated_at: datetime | None = None
    rebuild_in_progress: bool = False
    rebuild_error: str | None = None


class ThreadContextNotesUpdate(BaseModel):
    user_notes: str = Field(default="", max_length=20_000)
    expected_version: int = Field(ge=0)
