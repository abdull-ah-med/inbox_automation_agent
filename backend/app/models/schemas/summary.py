"""Per-message structured summary schemas (Haiku output)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MessageSummarySchema(BaseModel):
    """Structured Haiku summary of a single cleaned email body."""

    intent: str = Field(description="Short description of the sender's intent")
    ask: str | None = Field(
        default=None,
        description="What is being requested, or null if nothing is asked",
    )
    commitments: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    deadlines: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    one_line: str = Field(description="Single sentence for compact prompt lines")
