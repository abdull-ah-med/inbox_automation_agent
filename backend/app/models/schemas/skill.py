"""Pydantic schemas for editable skills (standing draft instructions)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.schemas.routing import RoutingCategory

SkillSourceKind = Literal["inline", "imported"]
SkillFileKind = Literal["reference", "asset"]


class SkillCreateSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    content: str = Field(min_length=1, max_length=10_000)
    category: RoutingCategory | None = None
    always_apply: bool = False
    is_active: bool = True

    @model_validator(mode="after")
    def _require_category_unless_always(self) -> SkillCreateSchema:
        if not self.always_apply and self.category is None:
            raise ValueError("category is required when always_apply is false")
        return self


class SkillUpdateSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    content: str | None = Field(default=None, min_length=1, max_length=50_000)
    category: RoutingCategory | None = None
    always_apply: bool | None = None
    is_active: bool | None = None


class SkillFileMetaSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    relative_path: str
    kind: SkillFileKind
    mime_type: str
    size_bytes: int
    created_at: datetime


class SkillResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    content: str
    category: str | None = None
    always_apply: bool = False
    is_active: bool
    source_kind: SkillSourceKind = "inline"
    imported_zip_sha256: str | None = None
    raw_frontmatter: dict[str, Any] | None = None
    reference_file_count: int = 0
    asset_file_count: int = 0
    created_at: datetime
    updated_at: datetime


class ImportSkillResultSchema(BaseModel):
    skill_id: uuid.UUID
    name: str
    description: str
    reference_files: list[str]
    asset_files: list[str]
    warnings: list[str]
    overwritten: bool = False
