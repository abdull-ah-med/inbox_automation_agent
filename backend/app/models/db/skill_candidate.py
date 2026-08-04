import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class SkillCandidate(Base):
    __tablename__ = "skill_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    routing_category: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    proposed_name: Mapped[str] = mapped_column(String(255), nullable=False)
    proposed_content: Mapped[str] = mapped_column(Text, nullable=False)
    source_rejection_ids: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="[]",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
