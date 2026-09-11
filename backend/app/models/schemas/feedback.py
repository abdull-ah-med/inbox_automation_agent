"""Pydantic schemas for draft feedback and regeneration."""

from __future__ import annotations

import uuid
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.schemas.routing import RejectReasonCode

ApprovalScope = Literal["once", "similar", "sender_address", "mailbox"]


class DraftApproveSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    edited_body: str | None = Field(default=None, max_length=50_000)
    approval_note: str | None = Field(default=None, max_length=2_000)
    approval_scope: ApprovalScope | None = None

    @model_validator(mode="after")
    def require_scope_when_note_present(self) -> Self:
        note = (self.approval_note or "").strip()
        if note and self.approval_scope is None:
            raise ValueError("approval_scope is required when approval_note is provided")
        if not note:
            self.approval_note = None
        return self


class DraftRejectSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    feedback_note: str = Field(min_length=1, max_length=5_000)
    reason_code: RejectReasonCode
    process_note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def empty_process_note_becomes_none(self) -> Self:
        note = (self.process_note or "").strip()
        self.process_note = note or None
        return self


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

    instruction: str = Field(min_length=1, max_length=2_000)


class DraftRegenAcceptedSchema(BaseModel):
    """202 body after rewrite is queued — poll thread detail until idle."""

    status: Literal["running"] = "running"
    thread_id: uuid.UUID
    draft_regen_in_progress: bool = True
    draft_regen_error: str | None = None
