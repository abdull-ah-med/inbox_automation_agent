"""Skill candidate proposals from recurring rejection clusters."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SkillCandidateProposeSchema(BaseModel):
    """Haiku output when proposing a standing skill from reject notes."""

    proposed_name: str = Field(min_length=1, max_length=255)
    proposed_content: str = Field(min_length=1, max_length=10_000)


class SkillCandidateResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mailbox: str
    routing_category: str
    reason_code: str
    proposed_name: str
    proposed_content: str
    source_rejection_ids: list[uuid.UUID] = Field(default_factory=list)
    status: Literal["pending", "accepted", "dismissed"]
    created_at: datetime


class SkillSelectionSchema(BaseModel):
    """Haiku output for which candidate skills apply to this draft."""

    applicable_skill_ids: list[uuid.UUID] = Field(default_factory=list)
