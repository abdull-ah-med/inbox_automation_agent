from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SuggestedRecipientSchema(BaseModel):
    role: str
    rationale: str


class DraftSchema(BaseModel):
    subject_line: str
    reply_body: str
    suggested_recipients: list[SuggestedRecipientSchema] = Field(default_factory=list)
    forward_to: str | None = None
    teaching_note: str
    urgency: Literal["CRITICAL", "HIGH", "NORMAL", "LOW"]
    urgency_reason: str


class DraftResponseSchema(DraftSchema):
    id: UUID
    thread_id: UUID
    created_at: datetime
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    edited_body: str | None = None
