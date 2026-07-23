"""Schemas for email embedding storage and similarity search."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class EmbeddingMatchSchema(BaseModel):
    """A ranked pgvector cosine similarity hit."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: str
    similarity_score: float = Field(ge=0.0, le=1.0)
    message_id: uuid.UUID | None = None
