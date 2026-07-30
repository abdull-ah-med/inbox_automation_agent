import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
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
    suggested_actions: Mapped[list[Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        server_default="'[]'",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
