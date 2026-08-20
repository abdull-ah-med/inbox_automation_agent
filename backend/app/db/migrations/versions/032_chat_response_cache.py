"""pgvector-backed semantic cache for InboxAssistant answers."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "032_chat_response_cache"
down_revision: str | None = "031_thread_summaries"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "chat_response_cache"
_HNSW = "chat_response_cache_embedding_hnsw_idx"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if _TABLE not in tables:
        op.create_table(
            _TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mailbox_key", sa.Text(), nullable=False),
            sa.Column("user_key", sa.Text(), nullable=False),
            sa.Column("query_normalized", sa.Text(), nullable=False),
            sa.Column("query_embedding", Vector(1536), nullable=False),
            sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column(
                "citation_thread_ids",
                postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                server_default=sa.text("'{}'"),
                nullable=False,
            ),
            sa.Column("hits", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_chat_response_cache_mailbox_expires",
            _TABLE,
            ["mailbox_key", "expires_at"],
        )
        op.create_index(
            "ix_chat_response_cache_citation_thread_ids",
            _TABLE,
            ["citation_thread_ids"],
            postgresql_using="gin",
        )

    inspector = sa.inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if _HNSW not in indexes:
        with op.get_context().autocommit_block():
            op.create_index(
                _HNSW,
                _TABLE,
                ["query_embedding"],
                unique=False,
                postgresql_using="hnsw",
                postgresql_ops={"query_embedding": "vector_cosine_ops"},
                postgresql_with={"m": 16, "ef_construction": 200},
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    indexes = {i["name"] for i in inspector.get_indexes(_TABLE)}
    with op.get_context().autocommit_block():
        if _HNSW in indexes:
            op.drop_index(_HNSW, table_name=_TABLE, postgresql_concurrently=True)
    op.drop_index("ix_chat_response_cache_citation_thread_ids", table_name=_TABLE)
    op.drop_index("ix_chat_response_cache_mailbox_expires", table_name=_TABLE)
    op.drop_table(_TABLE)
