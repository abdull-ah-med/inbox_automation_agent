"""pg_trgm GIN indexes for leading-wildcard ILIKE filters.

Revision ID: 037_pg_trgm_like_filters
Revises: 036_hnsw_swap_zero_downtime

embedding_repo._apply_column_filters uses ``ILIKE '%term%'`` on
email_embeddings.body_preview / search_document and threads.subject.
A leading wildcard cannot use btree; gin_trgm_ops makes those scans
index-backed.

See SQLAlchemy/Alembic ``only-concurrent-indexes`` and PostgreSQL
pg_trgm: https://www.postgresql.org/docs/current/pgtrgm.html
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "037_pg_trgm_like_filters"
down_revision: str | None = "036_hnsw_swap_zero_downtime"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_BODY = "ix_email_embeddings_body_preview_trgm"
_DOC = "ix_email_embeddings_search_document_trgm"
_SUBJECT = "ix_threads_subject_trgm"


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "email_embeddings" in tables:
        existing = {i["name"] for i in inspector.get_indexes("email_embeddings")}
        if _BODY not in existing:
            with op.get_context().autocommit_block():
                op.create_index(
                    _BODY,
                    "email_embeddings",
                    ["body_preview"],
                    unique=False,
                    postgresql_using="gin",
                    postgresql_ops={"body_preview": "gin_trgm_ops"},
                    postgresql_concurrently=True,
                )
        if _DOC not in existing:
            with op.get_context().autocommit_block():
                op.create_index(
                    _DOC,
                    "email_embeddings",
                    ["search_document"],
                    unique=False,
                    postgresql_using="gin",
                    postgresql_ops={"search_document": "gin_trgm_ops"},
                    postgresql_concurrently=True,
                )
    if "threads" in tables:
        existing = {i["name"] for i in sa.inspect(bind).get_indexes("threads")}
        if _SUBJECT not in existing:
            with op.get_context().autocommit_block():
                op.create_index(
                    _SUBJECT,
                    "threads",
                    ["subject"],
                    unique=False,
                    postgresql_using="gin",
                    postgresql_ops={"subject": "gin_trgm_ops"},
                    postgresql_concurrently=True,
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "email_embeddings" in tables:
        names = {i["name"] for i in inspector.get_indexes("email_embeddings")}
        for name in (_BODY, _DOC):
            if name in names:
                with op.get_context().autocommit_block():
                    op.drop_index(
                        name,
                        table_name="email_embeddings",
                        postgresql_concurrently=True,
                    )
    if "threads" in tables:
        names = {i["name"] for i in sa.inspect(bind).get_indexes("threads")}
        if _SUBJECT in names:
            with op.get_context().autocommit_block():
                op.drop_index(
                    _SUBJECT,
                    table_name="threads",
                    postgresql_concurrently=True,
                )
