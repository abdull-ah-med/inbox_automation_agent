"""Embedding repository: mocked unit tests plus Postgres oracles (skip if DB down)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, text

from app.core.config import Settings
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
    row.mailbox = "elise@example.com"
    row.body_preview = "hi"
    row.sender_email = "vendor@example.com"
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
        mailbox="elise@example.com",
        exclude_conversation_id="c-self",
    )

    assert len(matches) == 1
    assert matches[0].conversation_id == "c-match"
    assert matches[0].similarity_score == pytest.approx(0.90)
    assert matches[0].message_id == keep.message_id


@pytest.mark.asyncio
async def test_search_similar_sets_local_ef_search_from_settings() -> None:
    """Query-time recall knob must be SET LOCAL so pooled connections do not leak it."""
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    settings = Settings(environment="local", hnsw_ef_search=160, _env_file=None)

    await embedding_repo.search_similar(
        session,
        embedding=_vector(),
        min_similarity=0.78,
        top_k=3,
        mailbox="elise@example.com",
        settings=settings,
    )

    first_call = session.execute.await_args_list[0]
    sql = str(getattr(first_call.args[0], "text", first_call.args[0])).lower()
    assert "set_config('hnsw.ef_search'" in sql
    assert ", true)" in sql  # is_local — transaction-scoped, not session
    assert first_call.args[1]["ef_search"] == "160"


@pytest.mark.asyncio
async def test_search_similar_enables_iterative_scan_when_configured() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    settings = Settings(
        environment="local",
        hnsw_iterative_scan_enabled=True,
        _env_file=None,
    )

    await embedding_repo.search_similar(
        session,
        embedding=_vector(),
        min_similarity=0.78,
        top_k=3,
        mailbox="elise@example.com",
        settings=settings,
    )

    sqls = [
        str(getattr(call.args[0], "text", call.args[0])).lower()
        for call in session.execute.await_args_list
    ]
    assert any("set_config('hnsw.iterative_scan'" in sql and "strict_order" in sql for sql in sqls)


@pytest.mark.asyncio
async def test_search_similar_skips_iterative_scan_when_disabled() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    settings = Settings(
        environment="local",
        hnsw_iterative_scan_enabled=False,
        _env_file=None,
    )

    await embedding_repo.search_similar(
        session,
        embedding=_vector(),
        min_similarity=0.78,
        top_k=3,
        mailbox="elise@example.com",
        settings=settings,
    )

    sqls = [
        str(getattr(call.args[0], "text", call.args[0])).lower()
        for call in session.execute.await_args_list
    ]
    assert not any("iterative_scan" in sql for sql in sqls)


@pytest.mark.asyncio
async def test_search_fts_returns_empty_for_blank_query() -> None:
    session = AsyncMock()
    matches = await embedding_repo.search_fts(
        session,
        query_text="   ",
        top_k=5,
        exclude_conversation_id="c-self",
    )
    assert matches == []
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_fts_orders_by_rank_and_excludes_conversation() -> None:
    session = AsyncMock()
    keep = _row(conversation_id="c-match", message_id=uuid.uuid4())
    result = MagicMock()
    result.all.return_value = [(keep, 0.42)]
    session.execute = AsyncMock(return_value=result)

    matches = await embedding_repo.search_fts(
        session,
        query_text="intake packet deadline",
        top_k=5,
        mailbox="elise@example.com",
        exclude_conversation_id="c-self",
    )

    assert len(matches) == 1
    assert matches[0].conversation_id == "c-match"
    assert matches[0].message_id == keep.message_id
    assert matches[0].similarity_score == pytest.approx(min(1.0, 0.42))


@pytest.mark.asyncio
async def test_insert_embedding_without_message_pk_adds_row() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    new_id = uuid.uuid4()

    with patch("app.repositories.embedding_repo.EmailEmbedding") as emb_cls:
        row = MagicMock()
        row.id = new_id
        row.conversation_id = "c1"
        row.message_id = None
        emb_cls.return_value = row
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
            search_document="doc",
            embed_clean_version=1,
        )

    assert result.id == new_id
    assert result.conversation_id == "c1"


@pytest.mark.asyncio
async def test_update_embedding_document_updates_row() -> None:
    session = AsyncMock()
    row = _row()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()

    await embedding_repo.update_embedding_document(
        session,
        embedding_id=row.id,
        embedding=_vector(fill=0.2),
        search_document="updated doc",
        body_preview="preview",
        embed_clean_version=1,
    )

    assert row.search_document == "updated doc"
    assert row.body_preview == "preview"
    assert row.embed_clean_version == 1
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_similar_empty_mailboxes_does_not_query() -> None:
    session = AsyncMock()
    matches = await embedding_repo.search_similar(
        session,
        embedding=_vector(),
        min_similarity=0.78,
        top_k=3,
        mailboxes=[],
    )
    assert matches == []
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_fts_mailboxes_in_clause() -> None:
    session = AsyncMock()
    keep = _row(conversation_id="c-match", message_id=uuid.uuid4())
    result = MagicMock()
    result.all.return_value = [(keep, 0.42)]
    session.execute = AsyncMock(return_value=result)

    matches = await embedding_repo.search_fts(
        session,
        query_text="intake packet",
        top_k=5,
        mailboxes=["elise@example.com", "sales@example.com"],
    )

    assert len(matches) == 1
    assert matches[0].mailbox == "elise@example.com"


_PG_DIM = 1536
_PG_MAILBOX = "elise@example.com"
_PG_OTHER = "sales@example.com"


def _axis(index: int) -> list[float]:
    vec = [0.0] * _PG_DIM
    vec[index % _PG_DIM] = 1.0
    return vec


async def _wipe_embeddings(session) -> None:
    await session.execute(text("DELETE FROM email_embeddings"))
    await session.flush()


def _embedding_row(
    *,
    mailbox: str,
    conversation_id: str,
    embedding: list[float],
    search_document: str,
    sent_at: datetime | None = None,
) -> EmailEmbedding:
    return EmailEmbedding(
        mailbox=mailbox,
        conversation_id=conversation_id,
        sender_email="vendor@example.com",
        recipient_emails=[mailbox],
        cc_emails=[],
        embedding=embedding,
        sent_at=sent_at or datetime(2026, 8, 14, tzinfo=UTC),
        body_preview=search_document,
        search_document=search_document,
    )


@pytest.mark.asyncio
async def test_search_similar_keeps_near_vector_and_excludes_far_other_and_self(
    db_session,
) -> None:
    """Worked example: e0 vs e1 unit vectors; only same-mailbox e0 survives 0.5."""
    await _wipe_embeddings(db_session)
    e0, e1 = _axis(0), _axis(1)
    db_session.add_all(
        [
            _embedding_row(
                mailbox=_PG_MAILBOX,
                conversation_id="keep",
                embedding=e0,
                search_document="keep near",
            ),
            _embedding_row(
                mailbox=_PG_MAILBOX,
                conversation_id="far",
                embedding=e1,
                search_document="far orthogonal",
            ),
            _embedding_row(
                mailbox=_PG_OTHER,
                conversation_id="other-mailbox",
                embedding=e0,
                search_document="other mailbox near",
            ),
            _embedding_row(
                mailbox=_PG_MAILBOX,
                conversation_id="c-self",
                embedding=e0,
                search_document="excluded conversation",
            ),
        ]
    )
    await db_session.flush()

    matches = await embedding_repo.search_similar(
        db_session,
        embedding=e0,
        min_similarity=0.5,
        top_k=10,
        mailbox=_PG_MAILBOX,
        exclude_conversation_id="c-self",
        settings=Settings(environment="local", _env_file=None),
    )
    assert [m.conversation_id for m in matches] == ["keep"]
    assert matches[0].similarity_score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_search_fts_returns_only_scoped_mailbox(db_session) -> None:
    await _wipe_embeddings(db_session)
    db_session.add_all(
        [
            _embedding_row(
                mailbox=_PG_MAILBOX,
                conversation_id="keep-intake",
                embedding=_axis(0),
                search_document="Please send the intake packet deadline today.",
            ),
            _embedding_row(
                mailbox=_PG_MAILBOX,
                conversation_id="c-self",
                embedding=_axis(3),
                search_document="Please send the intake packet deadline today.",
            ),
            _embedding_row(
                mailbox=_PG_MAILBOX,
                conversation_id="sales-invoice",
                embedding=_axis(1),
                search_document="Unrelated invoice overdue reminder.",
            ),
            _embedding_row(
                mailbox=_PG_OTHER,
                conversation_id="other-intake",
                embedding=_axis(2),
                search_document="Please send the intake packet deadline today.",
            ),
        ]
    )
    await db_session.flush()

    matches = await embedding_repo.search_fts(
        db_session,
        query_text="intake packet",
        top_k=5,
        mailbox=_PG_MAILBOX,
        exclude_conversation_id="c-self",
    )
    assert [m.conversation_id for m in matches] == ["keep-intake"]
    assert matches[0].mailbox == _PG_MAILBOX


@pytest.mark.asyncio
async def test_search_fts_mailboxes_allowlist_excludes_outsider(db_session) -> None:
    await _wipe_embeddings(db_session)
    outsider = "clientrelations@example.com"
    db_session.add_all(
        [
            _embedding_row(
                mailbox=_PG_MAILBOX,
                conversation_id="in-allowlist-a",
                embedding=_axis(0),
                search_document="Please send the intake packet deadline today.",
            ),
            _embedding_row(
                mailbox=_PG_OTHER,
                conversation_id="in-allowlist-b",
                embedding=_axis(1),
                search_document="Please send the intake packet deadline today.",
            ),
            _embedding_row(
                mailbox=outsider,
                conversation_id="outsider-intake",
                embedding=_axis(2),
                search_document="Please send the intake packet deadline today.",
            ),
        ]
    )
    await db_session.flush()

    matches = await embedding_repo.search_fts(
        db_session,
        query_text="intake packet",
        top_k=5,
        mailboxes=[_PG_MAILBOX, _PG_OTHER],
    )
    assert {m.conversation_id for m in matches} == {"in-allowlist-a", "in-allowlist-b"}


@pytest.mark.asyncio
async def test_insert_embedding_persists_search_document(db_session) -> None:
    await _wipe_embeddings(db_session)
    search_document = "From: vendor Subject: hi\n\nclean body"
    inserted = await embedding_repo.insert_embedding(
        db_session,
        embedding=_axis(0),
        mailbox=_PG_MAILBOX,
        conversation_id="c-insert",
        sender_email="vendor@example.com",
        recipient_emails=[_PG_MAILBOX],
        cc_emails=[],
        sent_at=datetime(2026, 8, 14, tzinfo=UTC),
        body_preview="hi",
        search_document=search_document,
        embed_clean_version=1,
    )
    await db_session.flush()

    stored = (
        await db_session.execute(
            select(EmailEmbedding.search_document).where(EmailEmbedding.id == inserted.id)
        )
    ).scalar_one()
    assert stored == search_document
    assert inserted.conversation_id == "c-insert"
    assert inserted.message_id is None
