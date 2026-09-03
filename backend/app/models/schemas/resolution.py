"""Resolution / resolution-feedback request bodies."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ResolutionFeedbackAction = Literal["reopen", "wrong_reason"]


class ResolveThreadSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    actions_taken: str = Field(min_length=1, max_length=2_000)
    involved: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="before")
    @classmethod
    def normalize_actions_taken(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        actions = str(data.get("actions_taken") or "").strip()
        if actions:
            return {**data, "actions_taken": actions}
        note = str(data.get("note") or "").strip()
        if note:
            return {**data, "actions_taken": note, "note": note}
        return data


class ResolutionFeedbackSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: ResolutionFeedbackAction
    note: str | None = Field(default=None, max_length=2_000)


class ResolveThreadResponse(BaseModel):
    state: str


class ResolutionFeedbackResponse(BaseModel):
    state: str
    action: ResolutionFeedbackAction
