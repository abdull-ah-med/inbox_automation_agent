"""Schemas for global recipient contact greeting names."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MailboxContactView(BaseModel):
    email: str
    full_name: str
    first_name: str
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class MailboxContactListResponse(BaseModel):
    items: list[MailboxContactView]
    total: int


class MailboxContactUpsertSchema(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    full_name: str = Field(default="", max_length=200)
    first_name: str = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class MailboxContactPatchSchema(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    full_name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
