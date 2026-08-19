"""Add thread_association_reviews for sibling/associated confirm and dismiss."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "027_thread_assoc_reviews"
down_revision: str | None = "026_sent_replies"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "thread_association_reviews" in tables:
        return

    op.create_table(
        "thread_association_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("related_thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("actor", sa.String(length=320), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_thread_id"], ["threads.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["related_thread_id"], ["threads.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_thread_id",
            "related_thread_id",
            name="uq_thread_association_reviews_pair",
        ),
    )
    op.create_index(
        "ix_thread_association_reviews_source_thread_id",
        "thread_association_reviews",
        ["source_thread_id"],
    )
    op.create_index(
        "ix_thread_association_reviews_related_thread_id",
        "thread_association_reviews",
        ["related_thread_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "thread_association_reviews" not in tables:
        return
    op.drop_index(
        "ix_thread_association_reviews_related_thread_id",
        table_name="thread_association_reviews",
    )
    op.drop_index(
        "ix_thread_association_reviews_source_thread_id",
        table_name="thread_association_reviews",
    )
    op.drop_table("thread_association_reviews")
