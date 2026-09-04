"""Add golden_set_cases table for promotion gate evaluation.

Each case is a hand-curated (email, expected_urgency / action / draft criteria)
triple used to gate scope promotions. Ships empty; humans curate 30-100 cases
per mailbox after the table lands.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "059_golden_set_cases"
down_revision: str | None = "058_urgency_rules_and_proposals"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "golden_set_cases"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if _TABLE not in tables:
        op.create_table(
            _TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mailbox", sa.String(255), nullable=False),
            sa.Column("email_text", sa.Text(), nullable=False),
            sa.Column("expected_urgency", sa.String(16), nullable=True),
            sa.Column("expected_action", sa.String(64), nullable=True),
            sa.Column(
                "expected_associations",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column("expected_draft_body_criteria", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_golden_set_cases_mailbox", _TABLE, ["mailbox"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_golden_set_cases_mailbox", table_name=_TABLE)
    op.drop_table(_TABLE)
