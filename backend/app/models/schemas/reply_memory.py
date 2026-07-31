"""Pydantic schemas for approved-reply tone memory (Settings UI)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReplyMemoryResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    mailbox: str
    reply_text: str
    original_email_preview: str | None = None
    is_excluded: bool = False
    created_at: datetime


class ReplyMemoryUpdateSchema(BaseModel):
    is_excluded: bool = Field(description="When true, skip this reply in tone RAG")
