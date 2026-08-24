"""Fold apostrophes in email_embeddings.search_vector so O'Mason matches Omason."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "028_fts_fold_apostrophes"
down_revision: str | None = "027_thread_assoc_reviews"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_GIN_INDEX = "ix_email_embeddings_search_vector_gin"
_FOLDED = (
    "to_tsvector('english', translate(search_document, "
    "chr(39) || U&'\\2019' || U&'\\2018' || chr(96), ''))"
)
_PLAIN = "to_tsvector('english', search_document)"


def _recreate_search_vector(expression: str) -> None:
    bind = op.get_bind()
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("email_embeddings")}
    if _GIN_INDEX in indexes:
        with op.get_context().autocommit_block():
            op.drop_index(
                _GIN_INDEX,
                table_name="email_embeddings",
                postgresql_concurrently=True,
            )
    op.execute(sa.text("ALTER TABLE email_embeddings DROP COLUMN IF EXISTS search_vector"))
    op.execute(
        sa.text(
            f"""
            ALTER TABLE email_embeddings
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS ({expression}) STORED
            """
        )
    )
    with op.get_context().autocommit_block():
        op.create_index(
            _GIN_INDEX,
            "email_embeddings",
            ["search_vector"],
            unique=False,
            postgresql_using="gin",
            postgresql_concurrently=True,
        )


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("email_embeddings")}
    if "search_vector" not in existing:
        return
    # Already-folded catalogs (this revision applied historically) must not
    # DROP+ADD again — that takes ACCESS EXCLUSIVE and rewrites the heap.
    # Remaining unfolder catalogs are rewritten online in 035_fts_apostrophe_online.
    bind = op.get_bind()
    folded = bind.execute(
        sa.text(
            "SELECT pg_get_expr(ad.adbin, ad.adrelid) "
            "FROM pg_attrdef ad "
            "JOIN pg_attribute a ON a.attrelid = ad.adrelid AND a.attnum = ad.adnum "
            "WHERE ad.adrelid = 'email_embeddings'::regclass "
            "AND a.attname = 'search_vector'"
        )
    ).scalar()
    if folded and "translate" in str(folded):
        return
    # Pre-028 catalogs: skip the blocking rewrite; 035 performs the fold.
    return


def downgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("email_embeddings")}
    if "search_vector" not in existing:
        return
    _recreate_search_vector(_PLAIN)
