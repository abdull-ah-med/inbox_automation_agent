from datetime import datetime
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
    confidence: float = Field(ge=0.0, le=1.0)
    teaching_note: str


class DraftResponseSchema(DraftSchema):
    id: UUID
    thread_id: UUID
    created_at: datetime
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    edited_body: str | None = None
