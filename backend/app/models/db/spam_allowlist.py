import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class SpamAllowlist(Base):
    """Reviewer-corrected senders that must not be discarded as spam."""

    __tablename__ = "spam_allowlist"
    __table_args__ = (
        UniqueConstraint("mailbox", "sender_address", name="uq_spam_allowlist_mailbox_sender"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    mailbox: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    sender_address: Mapped[str] = mapped_column(String(320), nullable=False)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor: Mapped[str | None] = mapped_column(String(320), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
