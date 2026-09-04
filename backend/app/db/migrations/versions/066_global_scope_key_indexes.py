"""Covering indexes for global paired-retrieval walks.

Non-global retrieval uses mailbox-leading indexes. Global walks filter
(scope, scope_key, is_active/status) with no mailbox, so they cannot use
ix_feedback_atoms_mailbox_scope_key / ix_teaching_notes_mailbox_scope_key.

Concurrent indexes run in autocommit_block (only-concurrent-indexes).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "066_global_scope_key_indexes"
down_revision: str | None = "065_feedback_loop_enum_checks"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_ATOMS = "feedback_atoms"
_NOTES = "teaching_notes"
_IX_ATOMS = "ix_feedback_atoms_scope_scope_key"
_IX_NOTES = "ix_teaching_notes_scope_scope_key"


def _indexes(inspector: sa.Inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        inspector = sa.inspect(bind)
        names = _indexes(inspector, _ATOMS)
        if _IX_ATOMS not in names:
            op.create_index(
                _IX_ATOMS,
                _ATOMS,
                ["scope", "scope_key", "is_active"],
                unique=False,
                postgresql_concurrently=True,
            )
        inspector = sa.inspect(bind)
        names = _indexes(inspector, _NOTES)
        if _IX_NOTES not in names:
            op.create_index(
                _IX_NOTES,
                _NOTES,
                ["scope", "scope_key", "status"],
                unique=False,
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        inspector = sa.inspect(bind)
        if _IX_ATOMS in _indexes(inspector, _ATOMS):
            op.drop_index(_IX_ATOMS, table_name=_ATOMS, postgresql_concurrently=True)
        inspector = sa.inspect(bind)
        if _IX_NOTES in _indexes(inspector, _NOTES):
            op.drop_index(_IX_NOTES, table_name=_NOTES, postgresql_concurrently=True)
