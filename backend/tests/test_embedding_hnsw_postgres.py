"""Postgres HNSW index + recall oracles. Skip only when Postgres is down."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.db.email_embedding import EmailEmbedding
from app.repositories import embedding_repo

DIM = 1536
MAILBOX = "elise@example.com"


def _unit(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index % DIM] = 1.0
    return vec


def _near_query(scale: float) -> list[float]:
    vec = [0.0] * DIM
    vec[0] = scale
    vec[1] = (1.0 - scale * scale) ** 0.5
    return vec


@pytest.mark.asyncio
async def test_hnsw_index_uses_ef_construction_200(db_session) -> None:
    result = await db_session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'email_embeddings' "
            "AND indexname = 'ix_email_embeddings_embedding_hnsw'"
        )
    )
    indexdef = result.scalar_one()
    assert "ef_construction='200'" in indexdef or "ef_construction=200" in indexdef
    assert "m='16'" in indexdef or "m=16" in indexdef


@pytest.mark.asyncio
async def test_search_similar_returns_five_known_neighbors(db_session) -> None:
    """Five vectors closest to the query must appear in top-10 (hand-placed)."""
    query = _unit(0)
    neighbor_ids: list[uuid.UUID] = []
    now = datetime(2026, 8, 14, tzinfo=UTC)
    for i in range(5):
        row_id = uuid.uuid4()
        neighbor_ids.append(row_id)
        db_session.add(
            EmailEmbedding(
                id=row_id,
                mailbox=MAILBOX,
                conversation_id=f"near-{i}",
                sender_email="vendor@example.com",
                recipient_emails=["elise@example.com"],
                cc_emails=[],
                embedding=_near_query(0.99 - i * 0.01),
                sent_at=now,
                body_preview=f"near {i}",
                search_document=f"near {i}",
            )
        )
    for i in range(45):
        db_session.add(
            EmailEmbedding(
                mailbox=MAILBOX,
                conversation_id=f"far-{i}",
                sender_email="other@example.com",
                recipient_emails=["elise@example.com"],
                cc_emails=[],
                embedding=_unit(10 + i),
                sent_at=now,
                body_preview=f"far {i}",
                search_document=f"far {i}",
            )
        )
    await db_session.flush()

    settings = Settings(environment="local", hnsw_ef_search=100, _env_file=None)
    matches = await embedding_repo.search_similar(
        db_session,
        embedding=query,
        min_similarity=0.0,
        top_k=10,
        mailbox=MAILBOX,
        settings=settings,
    )
    returned = {match.id for match in matches[:10]}
    missing = [str(nid) for nid in neighbor_ids if nid not in returned]
    assert missing == [], f"known neighbors missing from top-10: {missing}"
    await db_session.execute(text("TRUNCATE email_embeddings RESTART IDENTITY CASCADE"))
    await db_session.commit()
