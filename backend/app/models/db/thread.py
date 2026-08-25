import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = (
        UniqueConstraint("mailbox", "conversation_id", name="uq_threads_mailbox_conversation"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    mailbox: Mapped[str] = mapped_column(String(320), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="NEW")
    urgency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    urgency_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alert_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    alert_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    alert_sender_norm: Mapped[str | None] = mapped_column(String(320), nullable=True)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
