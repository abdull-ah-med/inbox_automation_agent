"""Rebuild email_embeddings HNSW with production m / ef_construction.

Defaults (m=16, ef_construction=64) are demo settings. Query-time ef_search
is set per statement in embedding_repo; this migration only rebuilds the graph.

Dropping the live HNSW index before CREATE INDEX CONCURRENTLY leaves ANN
queries on a sequential scan for the duration of the build. If the live
index already matches production settings, this revision is a no-op.
Catalogs that still have demo settings are rebuilt in 036_hnsw_swap_zero_downtime
(create v2 concurrently, rename swap, drop old concurrently).

SET LOCAL maintenance_work_mem does not survive CREATE INDEX CONCURRENTLY
(autocommit). The 2GB build budget lives in 036 as session SET + RESET.

hnsw.iterative_scan requires pgvector >= 0.8.0 (released 2024-10-30):
https://github.com/pgvector/pgvector/blob/v0.8.0/CHANGELOG.md
https://www.postgresql.org/about/news/pgvector-080-released-2952/

See SQLAlchemy/Alembic ``only-concurrent-indexes``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "030_hnsw_production"
down_revision: str | None = "029_spam_allowlist"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INDEX_NAME = "ix_email_embeddings_embedding_hnsw"


def _indexdef_is_production(indexdef: str | None) -> bool:
    if not indexdef:
        return False
    has_m = "m='16'" in indexdef or "m=16" in indexdef
    has_ef = "ef_construction='200'" in indexdef or "ef_construction=200" in indexdef
    return has_m and has_ef


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "email_embeddings" not in tables:
        return
    indexes = {i["name"] for i in inspector.get_indexes("email_embeddings")}
    if _INDEX_NAME not in indexes:
        # 005 did not create it; 036 creates the production index concurrently.
        return
    indexdef = bind.execute(
        sa.text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'email_embeddings' AND indexname = :name"
        ),
        {"name": _INDEX_NAME},
    ).scalar()
    if _indexdef_is_production(str(indexdef) if indexdef is not None else None):
        return
    # Demo-settings catalogs: do not DROP here. 036 builds v2 concurrently.


def downgrade() -> None:
    return
