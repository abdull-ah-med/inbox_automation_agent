"""Live-schema oracles for Plan 3 thread_contexts.

A leftover table from an earlier draft of 062 can exist without
extract_input_hash. Alembic then no-ops create_table and GET /context 500s.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db

REQUIRED_CONTEXT_COLUMNS = (
    "thread_id",
    "user_notes",
    "version",
    "extract_input_hash",
    "last_message_id_at_extract",
    "updated_at",
    "created_at",
    "extract_status",
    "extract_started_at",
    "last_extract_error",
)


async def test_thread_contexts_has_extract_input_hash(db_session) -> None:
    """thread_contexts must include extract_input_hash — the pack skip key."""
    names = set(
        (
            await db_session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'thread_contexts'
                    """
                )
            )
        ).scalars()
    )
    missing = [col for col in REQUIRED_CONTEXT_COLUMNS if col not in names]
    assert missing == []


def test_extract_status_revision_fits_alembic_version_varchar32() -> None:
    """alembic_version.version_num is VARCHAR(32); the long filename is not the id."""
    path = Path("app/db/migrations/versions/064_thread_context_extract_status.py")
    spec = importlib.util.spec_from_file_location("migration_064_ctx_extract_status", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "064_ctx_extract_status"
    assert len(module.revision) <= 32
