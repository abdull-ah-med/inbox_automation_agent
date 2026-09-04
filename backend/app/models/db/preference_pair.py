import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class PreferencePair(Base):
    __tablename__ = "preference_pairs"

    id: Mapped[uuid.UUID] = uuid_pk()
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_address: Mapped[str] = mapped_column(String(320), nullable=False)
    sender_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    routing_category: Mapped[str] = mapped_column(String(32), nullable=False)
    email_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    email_embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    chosen_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejected_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
