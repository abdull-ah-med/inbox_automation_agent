"""Drop low-selectivity boolean B-tree indexes.

``ix_skills_is_active`` (from 011) and any early ``ix_reply_embeddings_is_excluded``
are rarely used by the planner. Drop concurrently per project Alembic rules:
https://www.postgresql.org/docs/current/sql-createindex.html#SQL-CREATEINDEX-CONCURRENTLY
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "013_drop_bool_indexes"
down_revision: str | None = "012_reply_embed_excluded"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INDEXES: tuple[tuple[str, str], ...] = (
    ("skills", "ix_skills_is_active"),
    ("reply_embeddings", "ix_reply_embeddings_is_excluded"),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, name in _INDEXES:
        indexes = {i["name"] for i in inspector.get_indexes(table)}
        if name not in indexes:
            continue
        with op.get_context().autocommit_block():
            op.drop_index(name, table_name=table, postgresql_concurrently=True)


def downgrade() -> None:
    # Recreate only if missing — still low value; kept for alembic symmetry.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, name in _INDEXES:
        indexes = {i["name"] for i in inspector.get_indexes(table)}
        if name in indexes:
            continue
        with op.get_context().autocommit_block():
            op.create_index(
                name,
                table,
                ["is_active" if table == "skills" else "is_excluded"],
                postgresql_concurrently=True,
            )
