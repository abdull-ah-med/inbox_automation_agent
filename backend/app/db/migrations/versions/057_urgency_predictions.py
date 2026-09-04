"""Add urgency_predictions table for full probability vectors at triage time.

Stores per-level probability distributions so calibration drift can be detected
with KS-tests against a 7-day cold-start window.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "057_urgency_predictions"
down_revision: str | None = "056_teaching_notes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "urgency_predictions"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if _TABLE not in tables:
        op.create_table(
            _TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mailbox", sa.String(255), nullable=False),
            sa.Column("routing_category", sa.String(32), nullable=True),
            sa.Column("sender_domain", sa.String(255), nullable=False),
            sa.Column("predicted_urgency", sa.String(16), nullable=False),
            sa.Column(
                "probs",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column("alert_fingerprint", sa.String(64), nullable=True),
            sa.Column(
                "applied_rule_ids",
                postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                nullable=True,
            ),
            sa.Column("final_urgency", sa.String(16), nullable=False),
            sa.Column("elise_edited_to", sa.String(16), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["draft_id"], ["drafts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("draft_id", name="uq_urgency_predictions_draft_id"),
        )
        op.create_index(
            "ix_urgency_predictions_mailbox_created_at",
            _TABLE,
            ["mailbox", sa.text("created_at DESC")],
        )
        op.create_index(
            "ix_urgency_predictions_mailbox_domain",
            _TABLE,
            ["mailbox", "sender_domain"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_urgency_predictions_mailbox_domain", table_name=_TABLE)
    op.drop_index("ix_urgency_predictions_mailbox_created_at", table_name=_TABLE)
    op.drop_table(_TABLE)
