import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[uuid.UUID] = uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    recipients: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    teaching_note: Mapped[str] = mapped_column(Text, nullable=False)
    urgency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    urgency_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    edited_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feedback_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    feedback_reason_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    routing_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approval_note_persisted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    suggested_actions: Mapped[list[Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        server_default="'[]'",
    )
    tool_calls_json: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    applied_skills_json: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    __table_args__ = (
        Index(
            "ix_drafts_approved_at",
            text("approved_at DESC"),
            postgresql_where=text("approved_at IS NOT NULL"),
        ),
        Index("ix_drafts_thread_id_created_at", "thread_id", text("created_at DESC")),
    )
