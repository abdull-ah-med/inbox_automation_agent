"""Add skill_files, skill import metadata, and drafts.tool_calls_json.

- skills.source_kind / imported_zip_sha256 / raw_frontmatter
- skill_files table for references/ and assets/
- drafts.tool_calls_json for read_skill_reference observability
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "021_skill_bundled_files"
down_revision: str | None = "020_skill_routing_candidates"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column(
            "source_kind",
            sa.String(length=16),
            nullable=False,
            server_default="inline",
        ),
    )
    op.add_column(
        "skills",
        sa.Column("imported_zip_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "skills",
        sa.Column(
            "raw_frontmatter",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_skills_imported_zip_sha256",
        "skills",
        ["imported_zip_sha256"],
        unique=False,
    )

    op.create_table(
        "skill_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relative_path", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "relative_path", name="uq_skill_files_skill_path"),
    )
    op.create_index("ix_skill_files_skill_id", "skill_files", ["skill_id"])

    op.add_column(
        "drafts",
        sa.Column(
            "tool_calls_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("drafts", "tool_calls_json")
    op.drop_index("ix_skill_files_skill_id", table_name="skill_files")
    op.drop_table("skill_files")
    op.drop_index("ix_skills_imported_zip_sha256", table_name="skills")
    op.drop_column("skills", "raw_frontmatter")
    op.drop_column("skills", "imported_zip_sha256")
    op.drop_column("skills", "source_kind")
