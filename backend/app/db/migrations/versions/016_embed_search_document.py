"""Add search_document + generated tsvector GIN index to email_embeddings."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision id must be <= 32 chars (alembic_version.version_num).
revision: str = "016_embed_search_document"
down_revision: str | None = "015_message_summaries"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_GIN_INDEX = "ix_email_embeddings_search_vector_gin"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("email_embeddings")}

    if "search_document" not in existing:
        op.add_column(
            "email_embeddings",
            sa.Column(
                "search_document",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
        )
    if "embed_clean_version" not in existing:
        op.add_column(
            "email_embeddings",
            sa.Column("embed_clean_version", sa.SmallInteger(), nullable=True),
        )
    if "search_vector" not in existing:
        op.execute(
            sa.text(
                """
                ALTER TABLE email_embeddings
                ADD COLUMN search_vector tsvector
                GENERATED ALWAYS AS (to_tsvector('english', search_document)) STORED
                """
            )
        )

    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("email_embeddings")}
    if _GIN_INDEX not in indexes:
        with op.get_context().autocommit_block():
            op.create_index(
                _GIN_INDEX,
                "email_embeddings",
                ["search_vector"],
                unique=False,
                postgresql_using="gin",
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("email_embeddings")}
    if _GIN_INDEX in indexes:
        with op.get_context().autocommit_block():
            op.drop_index(
                _GIN_INDEX,
                table_name="email_embeddings",
                postgresql_concurrently=True,
            )
    existing = {c["name"] for c in sa.inspect(bind).get_columns("email_embeddings")}
    if "search_vector" in existing:
        op.execute(sa.text("ALTER TABLE email_embeddings DROP COLUMN IF EXISTS search_vector"))
    if "embed_clean_version" in existing:
        op.drop_column("email_embeddings", "embed_clean_version")
    if "search_document" in existing:
        op.drop_column("email_embeddings", "search_document")
