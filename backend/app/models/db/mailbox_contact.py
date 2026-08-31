"""Per-mailbox recipient alias for draft salutations.

PII policy: emails and names are stored in cleartext (same as threads.subject).
Logging redaction covers log events, not this application table — see
backend/.cursor/rules/project-context.mdc Data Privacy & Logging.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class MailboxContact(Base):
    """Reviewer-taught first/full name for a recipient email in one mailbox."""

    __tablename__ = "mailbox_contacts"
    __table_args__ = (
        UniqueConstraint("mailbox", "email", name="uq_mailbox_contacts_mailbox_email"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    mailbox: Mapped[str] = mapped_column(String(320), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
