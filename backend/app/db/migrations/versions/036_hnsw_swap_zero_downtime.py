"""Zero-downtime HNSW swap to production m=16 / ef_construction=200.

Revision ID: 036_hnsw_swap_zero_downtime
Revises: 035_fts_apostrophe_online

030 historically dropped ix_email_embeddings_embedding_hnsw before rebuilding
it, so pgvector fell back to sequential scans for the duration of CREATE
INDEX CONCURRENTLY. This revision keeps the live index until v2 is ready,
then renames.

SET maintenance_work_mem is session-level (not SET LOCAL): CREATE INDEX
CONCURRENTLY runs in an autocommit block, so SET LOCAL would not apply to
the build. RESET after the build so later statements on this connection
do not inherit 2GB.

hnsw.iterative_scan (mailbox-filtered ANN) requires pgvector >= 0.8.0:
https://github.com/pgvector/pgvector/blob/v0.8.0/CHANGELOG.md
https://github.com/pgvector/pgvector#iterative-index-scans
https://www.postgresql.org/about/news/pgvector-080-released-2952/

pgvector HNSW: https://github.com/pgvector/pgvector#hnsw
See SQLAlchemy/Alembic ``only-concurrent-indexes``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "036_hnsw_swap_zero_downtime"
down_revision: str | None = "035_fts_apostrophe_online"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INDEX = "ix_email_embeddings_embedding_hnsw"
_INDEX_V2 = "ix_email_embeddings_embedding_hnsw_v2"
_TABLE = "email_embeddings"


def _indexdef_is_production(indexdef: str | None) -> bool:
    if not indexdef:
        return False
    has_m = "m='16'" in indexdef or "m=16" in indexdef
    has_ef = "ef_construction='200'" in indexdef or "ef_construction=200" in indexdef
    return has_m and has_ef


def _indexdef(bind: object, name: str) -> str | None:
    value = bind.execute(  # type: ignore[union-attr]
        sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = :table AND indexname = :name"),
        {"table": _TABLE, "name": name},
    ).scalar()
    return str(value) if value is not None else None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if _TABLE not in tables:
        return
    indexes = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if _INDEX in indexes and _indexdef_is_production(_indexdef(bind, _INDEX)):
        return

    op.execute(sa.text("SET maintenance_work_mem = '2GB'"))
    try:
        if _INDEX_V2 not in indexes:
            with op.get_context().autocommit_block():
                op.create_index(
                    _INDEX_V2,
                    _TABLE,
                    ["embedding"],
                    unique=False,
                    postgresql_using="hnsw",
                    postgresql_ops={"embedding": "vector_cosine_ops"},
                    postgresql_with={"m": 16, "ef_construction": 200},
                    postgresql_concurrently=True,
                )
        if _INDEX in indexes:
            op.execute(sa.text(f"ALTER INDEX {_INDEX} RENAME TO {_INDEX}_old"))
        op.execute(sa.text(f"ALTER INDEX {_INDEX_V2} RENAME TO {_INDEX}"))
        old_name = f"{_INDEX}_old"
        current = {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}
        if old_name in current:
            with op.get_context().autocommit_block():
                op.drop_index(
                    old_name,
                    table_name=_TABLE,
                    postgresql_concurrently=True,
                )
    finally:
        op.execute(sa.text("RESET maintenance_work_mem"))


def downgrade() -> None:
    # Production HNSW settings are a correctness/perf fix; do not restore
    # demo m/ef_construction.
    return
