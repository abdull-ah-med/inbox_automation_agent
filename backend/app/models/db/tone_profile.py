import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class ToneProfile(Base):
    __tablename__ = "tone_profiles"
    __table_args__ = (
        UniqueConstraint(
            "mailbox",
            "routing_category",
            name="uq_tone_profiles_mailbox_category",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    routing_category: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="general",
    )
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
