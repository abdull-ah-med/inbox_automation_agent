"""Add tone_profiles table for distilled mailbox+category voice rules."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "019_tone_profiles"
down_revision: str | None = "018_rejection_memories"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tone_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mailbox", sa.String(length=255), nullable=False),
        sa.Column(
            "routing_category",
            sa.String(length=32),
            nullable=False,
            server_default="general",
        ),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mailbox",
            "routing_category",
            name="uq_tone_profiles_mailbox_category",
        ),
    )
    op.create_index("ix_tone_profiles_mailbox", "tone_profiles", ["mailbox"])


def downgrade() -> None:
    op.drop_index("ix_tone_profiles_mailbox", table_name="tone_profiles")
    op.drop_table("tone_profiles")
