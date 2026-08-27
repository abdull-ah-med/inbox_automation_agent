"""Every vector search must SET LOCAL hnsw.ef_search before the ORDER BY."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.repositories import (
    chat_cache_repo,
    embedding_repo,
    rejection_memory_repo,
    reply_embedding_repo,
    skill_repo,
    urgency_feedback_repo,
)
from app.repositories._vector_common import set_hnsw_session_defaults

DIM = 1536
MAILBOX = "elise@example.com"


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        hnsw_ef_search=100,
        hnsw_iterative_scan_enabled=True,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        _env_file=None,
    )


def _unit() -> list[float]:
    vec = [0.0] * DIM
    vec[0] = 1.0
    return vec


class _CaptureSession:
    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.sql: list[str] = []

    async def execute(self, stmt: object, *args: object, **kwargs: object):
        self.sql.append(str(stmt))
        return await self._inner.execute(stmt, *args, **kwargs)


@pytest.mark.asyncio
async def test_all_vector_repos_set_ef_search(db_session) -> None:
    """H14: SET LOCAL hnsw.ef_search appears on every ANN query path."""
    wrapped = _CaptureSession(db_session)
    query = _unit()
    settings = _settings()

    await set_hnsw_session_defaults(wrapped, settings)  # type: ignore[arg-type]
    assert any("hnsw.ef_search" in sql for sql in wrapped.sql)

    wrapped.sql.clear()
    await embedding_repo.search_similar(
        wrapped,  # type: ignore[arg-type]
        embedding=query,
        min_similarity=0.0,
        top_k=1,
        mailbox=MAILBOX,
        settings=settings,
    )
    assert any("hnsw.ef_search" in sql for sql in wrapped.sql)

    wrapped.sql.clear()
    await chat_cache_repo.find_semantic_hit(
        wrapped,  # type: ignore[arg-type]
        mailbox_key=MAILBOX,
        user_key="reviewer",
        query_embedding=query,
        similarity_threshold=0.99,
        settings=settings,
    )
    assert any("hnsw.ef_search" in sql for sql in wrapped.sql)

    wrapped.sql.clear()
    await reply_embedding_repo.find_similar_replies(
        wrapped,  # type: ignore[arg-type]
        query_embedding=query,
        mailbox=MAILBOX,
        limit=1,
    )
    assert any("hnsw.ef_search" in sql for sql in wrapped.sql)

    wrapped.sql.clear()
    await rejection_memory_repo.find_similar_constraints(
        wrapped,  # type: ignore[arg-type]
        query_embedding=query,
        mailbox=MAILBOX,
        routing_category="sales",
        limit=1,
    )
    assert any("hnsw.ef_search" in sql for sql in wrapped.sql)

    wrapped.sql.clear()
    await urgency_feedback_repo.find_similar(
        wrapped,  # type: ignore[arg-type]
        query_embedding=query,
        mailbox=MAILBOX,
        limit=1,
    )
    assert any("hnsw.ef_search" in sql for sql in wrapped.sql)

    wrapped.sql.clear()
    await skill_repo.find_similar(
        wrapped,  # type: ignore[arg-type]
        embedding=query,
        threshold=0.9,
        limit=1,
    )
    assert any("hnsw.ef_search" in sql for sql in wrapped.sql)


@pytest.mark.asyncio
async def test_set_hnsw_session_defaults_is_local_not_session() -> None:
    """HNSW knobs must be transaction-local (set_config is_local=true)."""
    session = AsyncMock()
    session.execute = AsyncMock()
    await set_hnsw_session_defaults(session, _settings())
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    # set_config(name, value, is_local): third arg true = LOCAL to this txn
    assert any("set_config('hnsw.ef_search'" in sql and ", true)" in sql for sql in statements)
    assert any("set_config('hnsw.iterative_scan'" in sql and ", true)" in sql for sql in statements)
    params = session.execute.await_args_list[0].args[1]
    assert params["ef_search"] == "100"
