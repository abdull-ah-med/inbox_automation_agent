"""Add is_excluded flag on reply_embeddings for tone-memory opt-out.

Excluded rows stay stored for audit/history but are skipped by
find_similar_replies so they are not injected into Sonnet as tone refs.

No B-tree on ``is_excluded``: a boolean index has low selectivity and does
not help the HNSW + ``WHERE NOT is_excluded`` path (PostgreSQL planner).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Must stay ≤32 chars — alembic_version.version_num is VARCHAR(32).
revision: str = "012_reply_embed_excluded"
down_revision: str | None = "011_draft_feedback_skills"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reply_embeddings",
        sa.Column(
            "is_excluded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("reply_embeddings", "is_excluded")
