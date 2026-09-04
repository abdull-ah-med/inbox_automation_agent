import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class ThreadContext(Base):
    __tablename__ = "thread_contexts"

    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    extract_input_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default="",
    )
    last_message_id_at_extract: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    extract_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="idle",
    )
    extract_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_extract_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ThreadContextFact(Base):
    __tablename__ = "thread_context_facts"
    __table_args__ = (
        Index(
            "ix_thread_context_facts_thread_active_created",
            "thread_id",
            "created_at",
            postgresql_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("thread_context_facts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
