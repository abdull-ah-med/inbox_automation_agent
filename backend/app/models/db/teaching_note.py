import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class TeachingNote(Base):
    __tablename__ = "teaching_notes"

    id: Mapped[uuid.UUID] = uuid_pk()
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    applies_when: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    origin: Mapped[str] = mapped_column(String(24), nullable=False, server_default="manual")
    origin_atom_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("feedback_atoms.id"),
        nullable=True,
    )
    person_bound: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    precision_num: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    precision_den: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_teaching_notes_scope_scope_key", "scope", "scope_key", "status"),)
