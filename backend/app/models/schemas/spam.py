"""Schemas for reviewer spam corrections. Local DB only — never writes to Outlook."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class NotSpamResponseSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    thread_id: uuid.UUID
    state: str
    is_spam: bool = False
    sender_address: str = Field(min_length=3, max_length=320)
    outlook_unchanged: bool = True
