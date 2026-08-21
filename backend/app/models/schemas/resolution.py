"""Resolution / resolution-feedback request bodies."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ResolutionFeedbackAction = Literal["reopen", "wrong_reason"]


class ResolveThreadSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    note: str | None = Field(default=None, max_length=2_000)


class ResolutionFeedbackSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: ResolutionFeedbackAction
    note: str | None = Field(default=None, max_length=2_000)


class ResolveThreadResponse(BaseModel):
    state: str


class ResolutionFeedbackResponse(BaseModel):
    state: str
    action: ResolutionFeedbackAction
