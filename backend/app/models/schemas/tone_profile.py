"""Tone profile schema — distilled voice rules per mailbox + routing category."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ToneProfileSchema(BaseModel):
    """Tight, checkable tone profile distilled from approved replies."""

    formality: Literal["formal", "professional", "conversational"]
    greeting_pattern: str | None = None
    sign_off_pattern: str | None = None
    typical_length: Literal["short", "medium", "long"]
    favored_phrases: list[str] = Field(default_factory=list, max_length=8)
    avoided_phrases: list[str] = Field(default_factory=list, max_length=8)
    behavioral_rules: list[str] = Field(default_factory=list, max_length=8)


class ToneProfileResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mailbox: str
    routing_category: str
    profile: ToneProfileSchema
    sample_count: int
    version: int
    built_at: datetime
