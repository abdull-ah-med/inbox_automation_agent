import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class FeedbackAtom(Base):
    __tablename__ = "feedback_atoms"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False)
    atom_text: Mapped[str] = mapped_column(Text, nullable=False)
    atom_embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    role: Mapped[str] = mapped_column(String(8), nullable=False)
    applies_when: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(String(24), nullable=False, server_default="thread")
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    precision_num: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    precision_den: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    person_bound: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    promoted_from_atom_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("feedback_atoms.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_feedback_atoms_scope_scope_key", "scope", "scope_key", "is_active"),
    )
