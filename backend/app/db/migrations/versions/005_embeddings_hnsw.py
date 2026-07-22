"""Add HNSW index on email_embeddings.embedding for cosine similarity.

Per project db-patterns and pgvector docs: HNSW + vector_cosine_ops matches
``embedding.cosine_distance(...)`` queries. Created concurrently to avoid
long write locks when the table already has rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005_embeddings_hnsw"
down_revision: str | None = "004_drafts_drop_confidence"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INDEX_NAME = "ix_email_embeddings_embedding_hnsw"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes("email_embeddings")}
    if _INDEX_NAME in indexes:
        return

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
    with op.get_context().autocommit_block():
        op.create_index(
            _INDEX_NAME,
            "email_embeddings",
            ["embedding"],
            unique=False,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes("email_embeddings")}
    if _INDEX_NAME not in indexes:
        return

    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX_NAME,
            table_name="email_embeddings",
            postgresql_concurrently=True,
        )
