from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SuggestedRecipientSchema(BaseModel):
    role: str
    rationale: str


class SuggestedActionSchema(BaseModel):
    step: int
    action: str
    stakeholder: str | None = None
    rationale: str


class DraftSchema(BaseModel):
    subject_line: str
    reply_body: str
    suggested_recipients: list[SuggestedRecipientSchema] = Field(default_factory=list)
    forward_to: str | None = None
    teaching_note: str
    urgency: Literal["CRITICAL", "HIGH", "NORMAL", "LOW"]
    urgency_reason: str
    suggested_actions: list[SuggestedActionSchema] = Field(default_factory=list)


class DraftResponseSchema(DraftSchema):
    id: UUID
    thread_id: UUID
    created_at: datetime
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    edited_body: str | None = None
    # Retrieval similarity when Flow B injected a related thread (not LLM confidence).
    context_match_confidence: float | None = None
    feedback_note: str | None = None
    feedback_action: str | None = None
