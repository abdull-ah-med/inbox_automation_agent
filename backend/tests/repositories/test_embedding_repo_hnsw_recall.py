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
