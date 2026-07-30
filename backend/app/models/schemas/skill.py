"""Pydantic schemas for editable skills (standing draft instructions)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SkillCreateSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    content: str = Field(min_length=1, max_length=10_000)
    category: str | None = Field(default=None, max_length=64)
    is_active: bool = True


class SkillUpdateSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    content: str | None = Field(default=None, min_length=1, max_length=10_000)
    category: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


class SkillResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    content: str
    category: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
