"""Pydantic schemas for draft feedback and regeneration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas.routing import RejectReasonCode


class DraftApproveSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    edited_body: str | None = Field(default=None, max_length=50_000)


class DraftRejectSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    feedback_note: str = Field(min_length=1, max_length=5_000)
    reason_code: RejectReasonCode


class DraftWrongSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    feedback_note: str = Field(min_length=1, max_length=5_000)
    reason_code: RejectReasonCode | None = None


class DraftFeedbackSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: Literal["approve", "reject", "wrong"]
    note: str | None = Field(default=None, max_length=5_000)
    edited_body: str | None = Field(default=None, max_length=50_000)
    reason_code: RejectReasonCode | None = None


class RegenerateDraftSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    instruction: str = Field(min_length=1, max_length=500)
