"""Add drafts.applied_skills_json for skill snapshot on draft.

Revision ID: 022_draft_applied_skills
Revises: 021_skill_bundled_files
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "022_draft_applied_skills"
down_revision: str | None = "021_skill_bundled_files"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drafts",
        sa.Column(
            "applied_skills_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("drafts", "applied_skills_json")
