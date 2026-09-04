"""CHECK constraints for feedback-loop enum columns.

Atoms role/scope, teaching-note status/scope, urgency-rule status.
CHECK is NOT VALID then VALIDATE (split-check-constraint).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "065_feedback_loop_enum_checks"
down_revision: str | None = "064_ctx_extract_status"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_ATOMS = "feedback_atoms"
_NOTES = "teaching_notes"
_RULES = "urgency_rules"

_SCOPE_SQL = (
    "('thread', 'sender_address', 'sender_domain', 'mailbox+routing_category', 'mailbox', 'global')"
)

_CHECKS: tuple[tuple[str, str, str], ...] = (
    (_ATOMS, "ck_feedback_atoms_role", "role IN ('Fix', 'Spec', 'Null')"),
    (_ATOMS, "ck_feedback_atoms_scope", f"scope IN {_SCOPE_SQL}"),
    (_NOTES, "ck_teaching_notes_status", "status IN ('active', 'paused', 'archived')"),
    (_NOTES, "ck_teaching_notes_scope", f"scope IN {_SCOPE_SQL}"),
    (_RULES, "ck_urgency_rules_status", "status IN ('canary', 'active', 'paused', 'archived')"),
)


def _checks(inspector: sa.Inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_check_constraints(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table, name, sql in _CHECKS:
        if table not in tables:
            continue
        if name in _checks(inspector, table):
            continue
        op.create_check_constraint(name, table, sql, postgresql_not_valid=True)
        op.execute(sa.text(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}"))
        inspector = sa.inspect(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, name, _sql in reversed(_CHECKS):
        if name in _checks(inspector, table):
            op.drop_constraint(name, table, type_="check")
            inspector = sa.inspect(bind)
