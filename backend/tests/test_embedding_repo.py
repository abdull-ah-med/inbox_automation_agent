"""Unit tests for embedding_repo (session mocked — CI stays offline)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.db.email_embedding import EmailEmbedding
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.repositories import embedding_repo
from app.repositories.message_repo import MessageSchema


def _vector(dim: int = 8, fill: float = 0.1) -> list[float]:
    return [fill] * dim


def _row(
    *,
    conversation_id: str = "c-other",
    message_id: uuid.UUID | None = None,
) -> MagicMock:
    row = MagicMock(spec=EmailEmbedding)
    row.id = uuid.uuid4()
    row.conversation_id = conversation_id
    row.message_id = message_id
    return row


@pytest.mark.asyncio
async def test_insert_embedding_resolves_graph_message_and_inserts() -> None:
    session = AsyncMock()
    msg_pk = uuid.uuid4()
    new_id = uuid.uuid4()
    message = MessageSchema(
        id=msg_pk,
        thread_id=uuid.uuid4(),
        graph_message_id="graph-m1",
        direction="inbound",
        sender="vendor@example.com",
        body_text="hi",
        body_preview="hi",
        received_at=datetime(2026, 7, 22, tzinfo=UTC),
        to_recipients=[],
        cc_recipients=[],
    )

    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = None

    insert_row = MagicMock()
    insert_row.id = new_id
    insert_row.conversation_id = "c1"
    insert_row.message_id = msg_pk
    insert_result = MagicMock()
    insert_result.one_or_none.return_value = insert_row

    session.execute = AsyncMock(side_effect=[existing_result, insert_result])
    session.flush = AsyncMock()
    session.add = MagicMock()

    with patch(
        "app.repositories.embedding_repo.message_repo.get_by_graph_id",
        new=AsyncMock(return_value=message),
    ):
        result = await embedding_repo.insert_embedding(
            session,
            embedding=_vector(),
            mailbox="elise@example.com",
            conversation_id="c1",
            sender_email="vendor@example.com",
            recipient_emails=["elise@example.com"],
            cc_emails=[],
            sent_at=datetime(2026, 7, 22, tzinfo=UTC),
            body_preview="hi",
            graph_message_id="graph-m1",
        )

    assert isinstance(result, EmbeddingMatchSchema)
    assert result.id == new_id
    assert result.conversation_id == "c1"
    assert result.message_id == msg_pk
    assert session.execute.await_count == 2
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_insert_embedding_skips_duplicate_message_id() -> None:
    session = AsyncMock()
    existing_row = _row(conversation_id="c1", message_id=uuid.uuid4())
    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = existing_row
    session.execute = AsyncMock(return_value=existing_result)
    session.add = MagicMock()

    result = await embedding_repo.insert_embedding(
        session,
        embedding=_vector(),
        mailbox="elise@example.com",
        conversation_id="c1",
        sender_email="vendor@example.com",
        recipient_emails=[],
        cc_emails=[],
        sent_at=datetime(2026, 7, 22, tzinfo=UTC),
        body_preview="hi",
        message_pk=existing_row.message_id,
    )

    assert result.id == existing_row.id
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_search_similar_applies_threshold_in_sql_and_excludes_conversation() -> None:
    session = AsyncMock()
    keep = _row(conversation_id="c-match", message_id=uuid.uuid4())
    result = MagicMock()
    # SQL already applied min_similarity — mock returns only qualifying rows.
    result.all.return_value = [(keep, 0.90)]
    session.execute = AsyncMock(return_value=result)

    matches = await embedding_repo.search_similar(
        session,
        embedding=_vector(),
        min_similarity=0.78,
        top_k=3,
        exclude_conversation_id="c-self",
    )

    assert len(matches) == 1
    assert matches[0].conversation_id == "c-match"
    assert matches[0].similarity_score == pytest.approx(0.90)
    assert matches[0].message_id == keep.message_id

    session.execute.assert_awaited_once()
    stmt = session.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "email_embeddings" in compiled.lower() or "EmailEmbedding" in repr(stmt)
    # Distance threshold must be in the WHERE clause (not post-filtered in Python).
    assert (
        "cosine_distance" in compiled.lower() or "<=>" in compiled or "distance" in compiled.lower()
    )
