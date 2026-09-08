import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class PromotionProposal(Base):
    __tablename__ = "promotion_proposals"
    __table_args__ = (
        Index("ix_promotion_proposals_mailbox_status", "mailbox", "status"),
        Index(
            "uq_promotion_proposals_pending_dedupe",
            "mailbox",
            "kind",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    impact_num: Mapped[int] = mapped_column(Integer, nullable=False)
    impact_den: Mapped[int] = mapped_column(Integer, nullable=False)
    precision_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    precision_den: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default=text("'{}'"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    dedupe_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
