"""Drop redundant single-column indexes covered by composites.

PostgreSQL can satisfy leading-column predicates from a multicolumn B-tree
(https://www.postgresql.org/docs/current/indexes-multicolumn.html), so these
solo indexes only add write amplification:

- ix_skill_files_skill_id ← uq_skill_files_skill_path (skill_id, relative_path)
- ix_tone_profiles_mailbox ← uq_tone_profiles_mailbox_category
- ix_urgency_feedbacks_mailbox ← ix_urgency_feedbacks_mailbox_category

DROP INDEX CONCURRENTLY per project rule only-concurrent-indexes.md and
https://www.postgresql.org/docs/current/sql-dropindex.html

Revision ID: 050_drop_redundant_indexes
Revises: 049_mailbox_contacts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "050_drop_redundant_indexes"
down_revision: str | None = "049_mailbox_contacts"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# (table, index_name, recreate_columns for downgrade)
_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("skill_files", "ix_skill_files_skill_id", ["skill_id"]),
    ("tone_profiles", "ix_tone_profiles_mailbox", ["mailbox"]),
    ("urgency_feedbacks", "ix_urgency_feedbacks_mailbox", ["mailbox"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table, name, _cols in _INDEXES:
        inspector = sa.inspect(bind)
        existing = {idx["name"] for idx in inspector.get_indexes(table)}
        if name not in existing:
            continue
        with op.get_context().autocommit_block():
            op.drop_index(name, table_name=table, postgresql_concurrently=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table, name, cols in _INDEXES:
        inspector = sa.inspect(bind)
        existing = {idx["name"] for idx in inspector.get_indexes(table)}
        if name in existing:
            continue
        with op.get_context().autocommit_block():
            op.create_index(
                name,
                table,
                cols,
                postgresql_concurrently=True,
            )
