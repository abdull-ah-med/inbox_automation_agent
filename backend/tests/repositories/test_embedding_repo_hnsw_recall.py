"""HNSW recall@10 is monotonically non-decreasing as ef_search grows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.models.db.email_embedding import EmailEmbedding
from app.repositories import embedding_repo

DIM = 1536
MAILBOX = "elise@example.com"


def _unit(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index % DIM] = 1.0
    return vec


def _near(scale: float) -> list[float]:
    vec = [0.0] * DIM
    vec[0] = scale
    vec[1] = (1.0 - scale * scale) ** 0.5
    return vec


@pytest.mark.db
@pytest.mark.asyncio
async def test_hnsw_recall_at_10_is_non_decreasing_with_ef_search(db_session) -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    neighbors: list[uuid.UUID] = []
    for i in range(10):
        row_id = uuid.uuid4()
        neighbors.append(row_id)
        db_session.add(
            EmailEmbedding(
                id=row_id,
                mailbox=MAILBOX,
                conversation_id=f"near-{i}",
                sender_email="vendor@example.com",
                recipient_emails=["elise@example.com"],
                cc_emails=[],
                embedding=_near(0.99 - i * 0.005),
                sent_at=now,
                body_preview=f"near {i}",
                search_document=f"near {i}",
            )
        )
    for i in range(190):
        db_session.add(
            EmailEmbedding(
                mailbox=MAILBOX,
                conversation_id=f"far-{i}",
                sender_email="other@example.com",
                recipient_emails=["elise@example.com"],
                cc_emails=[],
                embedding=_unit(20 + i),
                sent_at=now,
                body_preview=f"far {i}",
                search_document=f"far {i}",
            )
        )
    await db_session.flush()
    query = _unit(0)
    recalls: list[int] = []
    for ef_search in (40, 100, 200):
        settings = Settings(
            environment="local",
            jwt_secret="c" * 64,
            frontend_origin="http://localhost:3000",
            cookie_secure=False,
            hnsw_ef_search=ef_search,
            database_url=(
                "postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test"
            ),
            redis_url="redis://localhost:6379/15",
            _env_file=None,
        )
        matches = await embedding_repo.search_similar(
            db_session,
            embedding=query,
            min_similarity=0.0,
            top_k=10,
            mailbox=MAILBOX,
            settings=settings,
        )
        found = {match.id for match in matches}
        recalls.append(len(found.intersection(neighbors)))
    assert recalls[0] <= recalls[1] <= recalls[2]
    assert recalls[-1] >= 8


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


@pytest.mark.db
@pytest.mark.asyncio
async def test_recall_meets_baseline_after_migration(db_session) -> None:
    """H7: HNSW top-10 recall vs brute-force cosine must be ≥ 0.9.

    Independent oracle: rank every fixture vector by hand cosine against
    the query; the ANN result must include at least 9 of those 10 ids.
    """
    from sqlalchemy import text

    now = datetime(2026, 8, 14, tzinfo=UTC)
    corpus: list[tuple[uuid.UUID, list[float]]] = []
    query = _unit(0)
    for i in range(10):
        row_id = uuid.uuid4()
        vec = _near(0.99 - i * 0.005)
        corpus.append((row_id, vec))
        db_session.add(
            EmailEmbedding(
                id=row_id,
                mailbox=MAILBOX,
                conversation_id=f"near-{i}",
                sender_email="vendor@example.com",
                recipient_emails=["elise@example.com"],
                cc_emails=[],
                embedding=vec,
                sent_at=now,
                body_preview=f"near {i}",
                search_document=f"near {i}",
            )
        )
    for i in range(90):
        row_id = uuid.uuid4()
        vec = _unit(20 + i)
        corpus.append((row_id, vec))
        db_session.add(
            EmailEmbedding(
                id=row_id,
                mailbox=MAILBOX,
                conversation_id=f"far-{i}",
                sender_email="other@example.com",
                recipient_emails=["elise@example.com"],
                cc_emails=[],
                embedding=vec,
                sent_at=now,
                body_preview=f"far {i}",
                search_document=f"far {i}",
            )
        )
    await db_session.flush()

    brute = sorted(corpus, key=lambda item: _cosine(query, item[1]), reverse=True)
    expected_ids = {row_id for row_id, _vec in brute[:10]}

    settings = Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        hnsw_ef_search=100,
        database_url=("postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test"),
        redis_url="redis://localhost:6379/15",
        _env_file=None,
    )
    matches = await embedding_repo.search_similar(
        db_session,
        embedding=query,
        min_similarity=0.0,
        top_k=10,
        mailbox=MAILBOX,
        settings=settings,
    )
    found = {match.id for match in matches}
    recall = len(found.intersection(expected_ids)) / 10
    assert recall >= 0.9, f"HNSW recall {recall} below 0.9 vs brute-force top-10"
    await db_session.execute(text("TRUNCATE email_embeddings RESTART IDENTITY CASCADE"))
    await db_session.commit()


@pytest.mark.db
@pytest.mark.asyncio
async def test_contains_filter_uses_trgm_index(db_session) -> None:
    """H8: leading-wildcard ILIKE on body_preview must use the trigram GIN.

    Independent oracle: EXPLAIN text names ix_email_embeddings_body_preview_trgm
    and does not seq-scan the table once seqscan is disabled.
    """
    from sqlalchemy import text

    now = datetime(2026, 8, 14, tzinfo=UTC)
    for i in range(80):
        db_session.add(
            EmailEmbedding(
                mailbox=MAILBOX,
                conversation_id=f"trgm-{i}",
                sender_email="vendor@example.com",
                recipient_emails=["elise@example.com"],
                cc_emails=[],
                embedding=_unit(i),
                sent_at=now,
                body_preview=(
                    "Please review the overdue billing packet."
                    if i == 0
                    else f"unrelated preview {i}"
                ),
                search_document=f"doc {i}",
            )
        )
    await db_session.commit()

    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan_rows = (
        (
            await db_session.execute(
                text(
                    "EXPLAIN (FORMAT TEXT) "
                    "SELECT id FROM email_embeddings "
                    "WHERE body_preview ILIKE :pattern"
                ),
                {"pattern": "%overdue billing%"},
            )
        )
        .scalars()
        .all()
    )
    plan = "\n".join(plan_rows)
    assert "ix_email_embeddings_body_preview_trgm" in plan, plan
    assert "Seq Scan" not in plan, plan
    await db_session.execute(text("TRUNCATE email_embeddings RESTART IDENTITY CASCADE"))
    await db_session.commit()


@pytest.mark.db
@pytest.mark.asyncio
async def test_recency_fallback_uses_composite_index(db_session) -> None:
    """H15: empty-query FTS recency path uses (mailbox, sent_at DESC)."""
    from sqlalchemy import text

    now = datetime(2026, 8, 14, tzinfo=UTC)
    for i in range(40):
        db_session.add(
            EmailEmbedding(
                mailbox=MAILBOX if i % 2 == 0 else "other@example.com",
                conversation_id=f"recency-{i}",
                sender_email="vendor@example.com",
                recipient_emails=["elise@example.com"],
                cc_emails=[],
                embedding=_unit(i),
                sent_at=now,
                body_preview=f"preview {i}",
                search_document=f"doc {i}",
            )
        )
    await db_session.commit()
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan_rows = (
        (
            await db_session.execute(
                text(
                    "EXPLAIN (FORMAT TEXT) "
                    "SELECT id FROM email_embeddings "
                    "WHERE mailbox = :mailbox "
                    "ORDER BY sent_at DESC "
                    "LIMIT 10"
                ),
                {"mailbox": MAILBOX},
            )
        )
        .scalars()
        .all()
    )
    plan = "\n".join(plan_rows)
    assert "ix_email_embeddings_mailbox_sent_at" in plan, plan
    await db_session.execute(text("TRUNCATE email_embeddings RESTART IDENTITY CASCADE"))
    await db_session.commit()
