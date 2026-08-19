"""Related-thread (sibling / associated) API contracts. Mail.Read only."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.schemas.urgency_feedback import UrgencyLevel

RelatedPurpose = Literal["siblings", "associated"]
RelatedReviewStatus = Literal["proposed", "confirmed", "dismissed"]
RelatedTreatment = Literal["no_reply", "urgency"]


class RelatedThreadItem(BaseModel):
    thread_id: uuid.UUID
    mailbox: str
    subject: str
    sender: str
    last_message_at: datetime | None = None
    urgency: str | None = None
    score: float
    status: RelatedReviewStatus = "proposed"


class RelatedThreadList(BaseModel):
    items: list[RelatedThreadItem] = Field(default_factory=list)


class ApplyTreatmentSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    treatment: RelatedTreatment
    thread_ids: list[uuid.UUID] = Field(default_factory=list)
    urgency: UrgencyLevel | None = None
    reason: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def require_urgency_level(self) -> Self:
        if self.treatment == "urgency" and self.urgency is None:
            raise ValueError("urgency is required when treatment is urgency")
        return self


class ApplyTreatmentResponse(BaseModel):
    applied_thread_ids: list[uuid.UUID] = Field(default_factory=list)


class RelatedReviewSchema(BaseModel):
    status: Literal["confirmed", "dismissed"]


class RelatedReviewResponse(BaseModel):
    status: Literal["confirmed", "dismissed"]
