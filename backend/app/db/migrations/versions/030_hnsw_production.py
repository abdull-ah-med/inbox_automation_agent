"""Rebuild email_embeddings HNSW with production m / ef_construction.

Defaults (m=16, ef_construction=64) are demo settings. Query-time ef_search
is set per statement in embedding_repo; this migration only rebuilds the graph.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "030_hnsw_production"
down_revision: str | None = "029_spam_allowlist"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INDEX_NAME = "ix_email_embeddings_embedding_hnsw"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "email_embeddings" not in tables:
        return
    indexes = {i["name"] for i in inspector.get_indexes("email_embeddings")}
    with op.get_context().autocommit_block():
        if _INDEX_NAME in indexes:
            op.drop_index(
                _INDEX_NAME,
                table_name="email_embeddings",
                postgresql_concurrently=True,
            )
        op.execute(sa.text("SET maintenance_work_mem = '2GB'"))
        op.create_index(
            _INDEX_NAME,
            "email_embeddings",
            ["embedding"],
            unique=False,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 200},
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "email_embeddings" not in tables:
        return
    indexes = {i["name"] for i in inspector.get_indexes("email_embeddings")}
    with op.get_context().autocommit_block():
        if _INDEX_NAME in indexes:
            op.drop_index(
                _INDEX_NAME,
                table_name="email_embeddings",
                postgresql_concurrently=True,
            )
        op.create_index(
            _INDEX_NAME,
            "email_embeddings",
            ["embedding"],
            unique=False,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_concurrently=True,
        )
