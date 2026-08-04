import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    graph_message_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_content_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="text",
        server_default="text",
    )
    body_clean: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_clean_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    body_clean_computed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Required for Haiku PoI To/CC rules on retry_triage rebuild from Postgres.
    to_recipients: Mapped[list[str]] = mapped_column(
        ARRAY(String(320)),
        nullable=False,
        default=list,
    )
    cc_recipients: Mapped[list[str]] = mapped_column(
        ARRAY(String(320)),
        nullable=False,
        default=list,
    )
    # Graph ``hasAttachments`` — flag only; attachment bytes are never stored.
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Structured Haiku summaries (Phase 2); null until summarized.
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    summary_one_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    summarized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    summary_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_clean_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
