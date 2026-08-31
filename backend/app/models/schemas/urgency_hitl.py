"""HITL feedback for automatic urgency bumps."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

UrgencyHitlAction = Literal["wrong_escalation"]


class UrgencyHitlFeedbackSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: UrgencyHitlAction
    note: str | None = Field(default=None, max_length=2_000)


class UrgencyHitlFeedbackResponse(BaseModel):
    state: str
    action: UrgencyHitlAction
    urgency: str | None = None
