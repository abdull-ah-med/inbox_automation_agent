import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db.base import Base, uuid_pk


class ChatResponseCache(Base):
    __tablename__ = "chat_response_cache"
    __table_args__ = (
        Index("ix_chat_response_cache_mailbox_expires", "mailbox_key", "expires_at"),
        Index(
            "ix_chat_response_cache_citation_thread_ids",
            "citation_thread_ids",
            postgresql_using="gin",
        ),
        Index(
            "chat_response_cache_embedding_hnsw_idx",
            "query_embedding",
            postgresql_using="hnsw",
            postgresql_ops={"query_embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 200},
        ),
        Index("ix_chat_response_cache_mailbox_user", "mailbox_key", "user_key"),
        Index(
            "uq_chat_response_cache_scope_query_hash",
            "mailbox_key",
            "user_key",
            text("md5(query_normalized)"),
            unique=True,
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_chat_response_cache_expires_after_created",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    mailbox_key: Mapped[str] = mapped_column(Text, nullable=False)
    user_key: Mapped[str] = mapped_column(Text, nullable=False)
    query_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    query_embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    response_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    citation_thread_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default=text("'{}'"),
    )
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
