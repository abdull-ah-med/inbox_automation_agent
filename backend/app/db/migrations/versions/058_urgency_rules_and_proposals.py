"""Add urgency_rules and promotion_proposals tables.

urgency_rules: Snorkel-style labelling functions evaluated post-model.
promotion_proposals: admin card queue for proposed scope widenings.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "058_urgency_rules_and_proposals"
down_revision: str | None = "057_urgency_predictions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_RULES_TABLE = "urgency_rules"
_PROPOSALS_TABLE = "promotion_proposals"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if _RULES_TABLE not in tables:
        op.create_table(
            _RULES_TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mailbox", sa.String(255), nullable=False),
            sa.Column("scope", sa.String(24), nullable=False),
            sa.Column("scope_key", sa.String(320), nullable=False),
            sa.Column(
                "condition",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column(
                "action",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'canary'"),
            ),
            sa.Column("canary_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("impact_num", sa.Integer(), nullable=True),
            sa.Column("impact_den", sa.Integer(), nullable=True),
            sa.Column("precision_num", sa.Integer(), nullable=True),
            sa.Column("precision_den", sa.Integer(), nullable=True),
            sa.Column(
                "hit_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "override_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "person_bound",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_urgency_rules_mailbox_status",
            _RULES_TABLE,
            ["mailbox", "status"],
        )

    if _PROPOSALS_TABLE not in tables:
        op.create_table(
            _PROPOSALS_TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mailbox", sa.String(255), nullable=False),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column(
                "payload",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column("impact_num", sa.Integer(), nullable=False),
            sa.Column("impact_den", sa.Integer(), nullable=False),
            sa.Column("precision_num", sa.Integer(), nullable=True),
            sa.Column("precision_den", sa.Integer(), nullable=True),
            sa.Column(
                "evidence_ids",
                postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column(
                "status",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column(
                "expires_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now() + interval '30 days'"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_promotion_proposals_mailbox_status",
            _PROPOSALS_TABLE,
            ["mailbox", "status"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if _PROPOSALS_TABLE in tables:
        op.drop_index("ix_promotion_proposals_mailbox_status", table_name=_PROPOSALS_TABLE)
        op.drop_table(_PROPOSALS_TABLE)

    if _RULES_TABLE in tables:
        op.drop_index("ix_urgency_rules_mailbox_status", table_name=_RULES_TABLE)
        op.drop_table(_RULES_TABLE)
