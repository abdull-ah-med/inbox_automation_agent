"""Store reviewer-authored process steps on rejected drafts.

Elise's correct process is JSONB on drafts, same shape as suggested_actions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Must stay ≤32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "068_draft_correct_actions"
down_revision: str | None = "067_ops_query_indexes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    draft_cols = {c["name"] for c in inspector.get_columns("drafts")}
    if "correct_actions" not in draft_cols:
        op.add_column(
            "drafts",
            sa.Column(
                "correct_actions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    draft_cols = {c["name"] for c in inspector.get_columns("drafts")}
    if "correct_actions" in draft_cols:
        op.drop_column("drafts", "correct_actions")
