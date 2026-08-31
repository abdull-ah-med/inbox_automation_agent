"""Fold apostrophes in search_vector without dropping the live FTS column.

Revision ID: 035_fts_apostrophe_online
Revises: 034_message_bcc

028 historically did ``DROP COLUMN search_vector`` + ``ADD COLUMN … GENERATED``
which takes ACCESS EXCLUSIVE and rewrites email_embeddings. That rewrite
already ran in environments that applied 028; this revision is a no-op there.

OPERATOR NOTE: PostgreSQL still takes ACCESS EXCLUSIVE to ADD a STORED
generated column. The improvement versus 028 is we never drop the live
``search_vector`` until the new GIN is ready, so FTS reads keep working
against the old column during the concurrent index build. Schedule the
remaining lock for a quiet window if this revision actually rewrites
(legacy catalogs that never folded).

See SQLAlchemy/Alembic ``change-column-type`` and ``only-concurrent-indexes``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "035_fts_apostrophe_online"
down_revision: str | None = "034_message_bcc"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_GIN_INDEX = "ix_email_embeddings_search_vector_gin"
_GIN_INDEX_V2 = "ix_email_embeddings_search_vector_v2_gin"
_FOLDED = (
    "to_tsvector('english', translate(search_document, "
    "chr(39) || U&'\\2019' || U&'\\2018' || chr(96), ''))"
)


def _generation_expr(column: str) -> str | None:
    bind = op.get_bind()
    return bind.execute(
        sa.text(
            "SELECT pg_get_expr(ad.adbin, ad.adrelid) "
            "FROM pg_attrdef ad "
            "JOIN pg_attribute a ON a.attrelid = ad.adrelid AND a.attnum = ad.adnum "
            "WHERE ad.adrelid = 'email_embeddings'::regclass "
            "AND a.attname = :col"
        ),
        {"col": column},
    ).scalar()


def _already_folded() -> bool:
    expr = _generation_expr("search_vector")
    return bool(expr) and "translate" in str(expr)


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("email_embeddings")}
    if "search_vector" not in existing:
        return
    if _already_folded():
        return

    if "search_vector_v2" not in existing:
        op.execute(
            sa.text(
                f"""
                ALTER TABLE email_embeddings
                ADD COLUMN search_vector_v2 tsvector
                GENERATED ALWAYS AS ({_FOLDED}) STORED
                """
            )
        )
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("email_embeddings")}
    if _GIN_INDEX_V2 not in indexes:
        with op.get_context().autocommit_block():
            op.create_index(
                _GIN_INDEX_V2,
                "email_embeddings",
                ["search_vector_v2"],
                unique=False,
                postgresql_using="gin",
                postgresql_concurrently=True,
            )
    op.execute(sa.text("ALTER TABLE email_embeddings DROP COLUMN search_vector"))
    op.execute(
        sa.text("ALTER TABLE email_embeddings RENAME COLUMN search_vector_v2 TO search_vector")
    )
    if _GIN_INDEX in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("email_embeddings")}:
        with op.get_context().autocommit_block():
            op.drop_index(
                _GIN_INDEX,
                table_name="email_embeddings",
                postgresql_concurrently=True,
            )
    op.execute(sa.text(f"ALTER INDEX {_GIN_INDEX_V2} RENAME TO {_GIN_INDEX}"))


def downgrade() -> None:
    # Folding is a correctness fix; do not restore the splitting apostrophe
    # expression. Leaving the folded column in place is safe.
    return
