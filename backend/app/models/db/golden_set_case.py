import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class GoldenSetCase(Base):
    __tablename__ = "golden_set_cases"

    id: Mapped[uuid.UUID] = uuid_pk()
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_urgency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    expected_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_associations: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    expected_draft_body_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
