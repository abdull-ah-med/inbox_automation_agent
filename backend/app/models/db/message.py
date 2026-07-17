import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    graph_message_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Required for Haiku PoI To/CC rules on retry_triage rebuild from Postgres.
    to_recipients: Mapped[list[str]] = mapped_column(
        ARRAY(String(320)),
        nullable=False,
        default=list,
    )
    cc_recipients: Mapped[list[str]] = mapped_column(
        ARRAY(String(320)),
        nullable=False,
        default=list,
    )
