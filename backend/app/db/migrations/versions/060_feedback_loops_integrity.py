"""Feedback-loops integrity: CHECKs, sender_domain, dedupe, previous_status, indexes.

Head was 059_golden_set_cases.

- golden_set_cases.sender_domain + (mailbox, sender_domain) index
- promotion_proposals.status CHECK pending|accepted|dismissed|expired|reverted
- promotion_proposals.dedupe_key + concurrent partial unique on pending
- urgency_rules.previous_status
- concurrent decay indexes
- replace 4-col atom index with 3-col (mailbox, scope, scope_key)

Concurrent indexes run in autocommit_block (only-concurrent-indexes).
CHECK is NOT VALID then VALIDATE (split-check-constraint).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "060_feedback_loops_integrity"
down_revision: str | None = "059_golden_set_cases"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_GOLDEN = "golden_set_cases"
_PROPOSALS = "promotion_proposals"
_RULES = "urgency_rules"
_ATOMS = "feedback_atoms"
_NOTES = "teaching_notes"

_CK_STATUS = "ck_promotion_proposals_status"
_UQ_PENDING = "uq_promotion_proposals_pending_dedupe"
_IX_GOLDEN_MAILBOX = "ix_golden_set_cases_mailbox"
_IX_GOLDEN_DOMAIN = "ix_golden_set_cases_mailbox_sender_domain"
_IX_ATOMS_OLD = "ix_feedback_atoms_mailbox_scope_key_active"
_IX_ATOMS_NEW = "ix_feedback_atoms_mailbox_scope_key"
_IX_ATOMS_DECAY = "ix_feedback_atoms_active_expires"
_IX_NOTES_DECAY = "ix_teaching_notes_status_expires"


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def _indexes(inspector: sa.Inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {i["name"] for i in inspector.get_indexes(table)}


def _checks(inspector: sa.Inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_check_constraints(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "sender_domain" not in _columns(inspector, _GOLDEN):
        op.add_column(_GOLDEN, sa.Column("sender_domain", sa.String(255), nullable=True))

    if "dedupe_key" not in _columns(inspector, _PROPOSALS):
        op.add_column(_PROPOSALS, sa.Column("dedupe_key", sa.String(64), nullable=True))

    if "previous_status" not in _columns(inspector, _RULES):
        op.add_column(_RULES, sa.Column("previous_status", sa.String(16), nullable=True))

    inspector = sa.inspect(bind)
    if _CK_STATUS not in _checks(inspector, _PROPOSALS):
        op.create_check_constraint(
            _CK_STATUS,
            _PROPOSALS,
            "status IN ('pending', 'accepted', 'dismissed', 'expired', 'reverted')",
            postgresql_not_valid=True,
        )
        op.execute(sa.text(f"ALTER TABLE {_PROPOSALS} VALIDATE CONSTRAINT {_CK_STATUS}"))

    with op.get_context().autocommit_block():
        inspector = sa.inspect(bind)
        tables = set(inspector.get_table_names())

        if _GOLDEN in tables:
            names = _indexes(inspector, _GOLDEN)
            if _IX_GOLDEN_DOMAIN not in names:
                op.create_index(
                    _IX_GOLDEN_DOMAIN,
                    _GOLDEN,
                    ["mailbox", "sender_domain"],
                    unique=False,
                    postgresql_concurrently=True,
                )
            inspector = sa.inspect(bind)
            names = _indexes(inspector, _GOLDEN)
            if _IX_GOLDEN_MAILBOX in names:
                op.drop_index(
                    _IX_GOLDEN_MAILBOX,
                    table_name=_GOLDEN,
                    postgresql_concurrently=True,
                )

        inspector = sa.inspect(bind)
        if _PROPOSALS in set(inspector.get_table_names()):
            names = _indexes(inspector, _PROPOSALS)
            if _UQ_PENDING not in names:
                op.create_index(
                    _UQ_PENDING,
                    _PROPOSALS,
                    ["mailbox", "kind", "dedupe_key"],
                    unique=True,
                    postgresql_concurrently=True,
                    postgresql_where=sa.text("status = 'pending'"),
                )

        inspector = sa.inspect(bind)
        if _ATOMS in set(inspector.get_table_names()):
            names = _indexes(inspector, _ATOMS)
            if _IX_ATOMS_DECAY not in names:
                op.create_index(
                    _IX_ATOMS_DECAY,
                    _ATOMS,
                    ["is_active", "expires_at"],
                    unique=False,
                    postgresql_concurrently=True,
                )
            inspector = sa.inspect(bind)
            names = _indexes(inspector, _ATOMS)
            if _IX_ATOMS_NEW not in names:
                op.create_index(
                    _IX_ATOMS_NEW,
                    _ATOMS,
                    ["mailbox", "scope", "scope_key"],
                    unique=False,
                    postgresql_concurrently=True,
                )
            inspector = sa.inspect(bind)
            names = _indexes(inspector, _ATOMS)
            if _IX_ATOMS_OLD in names:
                op.drop_index(
                    _IX_ATOMS_OLD,
                    table_name=_ATOMS,
                    postgresql_concurrently=True,
                )

        inspector = sa.inspect(bind)
        if _NOTES in set(inspector.get_table_names()):
            names = _indexes(inspector, _NOTES)
            if _IX_NOTES_DECAY not in names:
                op.create_index(
                    _IX_NOTES_DECAY,
                    _NOTES,
                    ["status", "expires_at"],
                    unique=False,
                    postgresql_concurrently=True,
                )


def downgrade() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        inspector = sa.inspect(bind)
        names = _indexes(inspector, _NOTES)
        if _IX_NOTES_DECAY in names:
            op.drop_index(_IX_NOTES_DECAY, table_name=_NOTES, postgresql_concurrently=True)

        inspector = sa.inspect(bind)
        names = _indexes(inspector, _ATOMS)
        if _IX_ATOMS_OLD not in names:
            op.create_index(
                _IX_ATOMS_OLD,
                _ATOMS,
                ["mailbox", "scope", "scope_key", "is_active"],
                unique=False,
                postgresql_concurrently=True,
            )
        inspector = sa.inspect(bind)
        names = _indexes(inspector, _ATOMS)
        if _IX_ATOMS_NEW in names:
            op.drop_index(_IX_ATOMS_NEW, table_name=_ATOMS, postgresql_concurrently=True)
        inspector = sa.inspect(bind)
        names = _indexes(inspector, _ATOMS)
        if _IX_ATOMS_DECAY in names:
            op.drop_index(_IX_ATOMS_DECAY, table_name=_ATOMS, postgresql_concurrently=True)

        inspector = sa.inspect(bind)
        names = _indexes(inspector, _PROPOSALS)
        if _UQ_PENDING in names:
            op.drop_index(_UQ_PENDING, table_name=_PROPOSALS, postgresql_concurrently=True)

        inspector = sa.inspect(bind)
        names = _indexes(inspector, _GOLDEN)
        if _IX_GOLDEN_MAILBOX not in names:
            op.create_index(
                _IX_GOLDEN_MAILBOX,
                _GOLDEN,
                ["mailbox"],
                unique=False,
                postgresql_concurrently=True,
            )
        inspector = sa.inspect(bind)
        names = _indexes(inspector, _GOLDEN)
        if _IX_GOLDEN_DOMAIN in names:
            op.drop_index(_IX_GOLDEN_DOMAIN, table_name=_GOLDEN, postgresql_concurrently=True)

    inspector = sa.inspect(bind)
    if _CK_STATUS in _checks(inspector, _PROPOSALS):
        op.drop_constraint(_CK_STATUS, _PROPOSALS, type_="check")
    if "previous_status" in _columns(inspector, _RULES):
        op.drop_column(_RULES, "previous_status")
    if "dedupe_key" in _columns(inspector, _PROPOSALS):
        op.drop_column(_PROPOSALS, "dedupe_key")
    if "sender_domain" in _columns(inspector, _GOLDEN):
        op.drop_column(_GOLDEN, "sender_domain")
