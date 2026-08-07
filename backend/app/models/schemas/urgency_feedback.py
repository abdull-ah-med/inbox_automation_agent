"""Schemas for manual urgency edit feedback."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

UrgencyLevel = Literal["CRITICAL", "HIGH", "NORMAL", "LOW"]


class UrgencyEditRequestSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    new_urgency: UrgencyLevel
    reason: str = Field(min_length=1, max_length=500)


class UrgencyEditResponseSchema(BaseModel):
    urgency: UrgencyLevel
    urgency_reason: str
    updated_at: datetime
    draft_id: UUID
    thread_id: UUID
