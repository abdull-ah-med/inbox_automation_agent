import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class ReplyEmbedding(Base):
    __tablename__ = "reply_embeddings"

    id: Mapped[uuid.UUID] = uuid_pk()
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    original_email_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    learning_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_excluded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
