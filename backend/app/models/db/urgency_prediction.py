import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class UrgencyPrediction(Base):
    __tablename__ = "urgency_predictions"

    id: Mapped[uuid.UUID] = uuid_pk()
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False)
    routing_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sender_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    predicted_urgency: Mapped[str] = mapped_column(String(16), nullable=False)
    probs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    alert_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    applied_rule_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )
    final_urgency: Mapped[str] = mapped_column(String(16), nullable=False)
    elise_edited_to: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
