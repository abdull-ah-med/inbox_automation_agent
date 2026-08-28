"""Pydantic schemas for rejection-memory Settings UI."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RejectionMemoryResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    thread_id: uuid.UUID | None = None
    mailbox: str
    routing_category: str
    reason_code: str
    note: str
    reason_text: str | None = None
    is_excluded: bool = False
    created_at: datetime | None = None
    draft_subject: str | None = None
    draft_body: str | None = None
    draft_body_preview: str | None = None
    preview_line: str | None = None
    sender_email: str | None = None
    receiver_email: str | None = None


class RejectionMemoryUpdateSchema(BaseModel):
    is_excluded: bool = Field(description="When true, skip this reject in negative-constraint RAG")
