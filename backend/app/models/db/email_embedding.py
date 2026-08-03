import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class EmailEmbedding(Base):
    __tablename__ = "email_embeddings"
    # Matches migration 006 — partial unique index (not a plain ``index=True``).
    __table_args__ = (
        Index(
            "uq_email_embeddings_message_id",
            "message_id",
            unique=True,
            postgresql_where=text("message_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    mailbox: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sender_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    recipient_emails: Mapped[list[str]] = mapped_column(
        ARRAY(String(320)),
        nullable=False,
    )
    cc_emails: Mapped[list[str]] = mapped_column(
        ARRAY(String(320)),
        nullable=False,
        default=list,
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body_preview: Mapped[str] = mapped_column(Text, nullable=False)
    search_document: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        server_default="",
    )
    # Generated tsvector is defined in migration 016; not mapped for ORM writes.
    embed_clean_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
