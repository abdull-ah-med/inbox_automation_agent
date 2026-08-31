"""Pydantic schemas for approved-reply tone memory (Settings UI)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReplyMemoryResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    thread_id: uuid.UUID | None = None
    mailbox: str
    reply_text: str
    preview_line: str | None = None
    draft_subject: str | None = None
    sender_email: str | None = None
    receiver_email: str | None = None
    reason_code: str | None = None
    reason_text: str | None = None
    original_email_preview: str | None = None
    learning_note: str | None = None
    is_excluded: bool = False
    created_at: datetime | None = None


class ReplyMemoryUpdateSchema(BaseModel):
    is_excluded: bool = Field(description="When true, skip this reply in tone RAG")
