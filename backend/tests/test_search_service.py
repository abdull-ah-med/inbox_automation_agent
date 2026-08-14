"""Hybrid thread search: blank queries, mailbox isolation, ranked hits.

Mailbox-leak expectations are literals from a worked example (sales vs CR vs
an outsider mailbox that is not in TARGET_MAILBOXES). Ranking expectations
reuse the RRF corroboration numbers from ``test_rrf`` (0.016129 vs 0.0203125),
not scores recomputed from the production formula in this file.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import EmptySearchQueryError, UnknownMailboxError
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.repositories.thread_repo import ThreadSchema
from app.services import search_service
from app.services.search_service import SEARCH_SNIPPET_MAX_CHARS, build_snippet

SALES = "sales@example.com"
CR = "cr@example.com"
OUTSIDER = "other@example.com"

SALES_THREAD_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CR_THREAD_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
OUTSIDER_THREAD_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
CONV_A_THREAD_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
CONV_B_THREAD_ID = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")

SALES_EMBEDDING_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
CR_EMBEDDING_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
OUTSIDER_EMBEDDING_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "local",
        "target_mailboxes": f"{SALES},{CR}",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 8,
        "embedding_min_similarity": 0.78,
        "embedding_candidate_k": 15,
        "embedding_corroboration_bonus": 0.15,
        "embedding_corroboration_max_hits": 3,
        "rrf_k": 60,
        "openai_api_key": "test-key",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _match(
    *,
    embedding_id: uuid.UUID,
    conversation_id: str,
    mailbox: str,
    body_preview: str,
    message_id: uuid.UUID | None = None,
    score: float = 0.9,
) -> EmbeddingMatchSchema:
    return EmbeddingMatchSchema(
        id=embedding_id,
        conversation_id=conversation_id,
        similarity_score=score,
        message_id=message_id or uuid.uuid4(),
        mailbox=mailbox,
        body_preview=body_preview,
    )


def _thread(
    *,
    thread_id: uuid.UUID,
    mailbox: str,
    conversation_id: str,
    subject: str,
    state: str = "DRAFTED",
    urgency: str | None = "HIGH",
    last_message_at: datetime | None = None,
) -> ThreadSchema:
    return ThreadSchema(
        id=thread_id,
        mailbox=mailbox,
        conversation_id=conversation_id,
        subject=subject,
        state=state,
        urgency=urgency,
        last_message_at=last_message_at or datetime(2026, 8, 5, 15, 0, tzinfo=UTC),
        last_updated_at=datetime(2026, 8, 5, 16, 0, tzinfo=UTC),
    )


SALES_MATCH = _match(
    embedding_id=SALES_EMBEDDING_ID,
    conversation_id="conv-sales-packet",
    mailbox=SALES,
    body_preview="Please send the drug screen packet by Friday.",
)
CR_MATCH = _match(
    embedding_id=CR_EMBEDDING_ID,
    conversation_id="conv-cr-invoice",
    mailbox=CR,
    body_preview="Following up on the unpaid invoice.",
)
OUTSIDER_MATCH = _match(
    embedding_id=OUTSIDER_EMBEDDING_ID,
    conversation_id="conv-outsider-packet",
    mailbox=OUTSIDER,
    body_preview="Secret drug screen packet for another tenant.",
)

ALL_MATCHES = [SALES_MATCH, CR_MATCH, OUTSIDER_MATCH]

THREADS = {
    (SALES, "conv-sales-packet"): _thread(
        thread_id=SALES_THREAD_ID,
        mailbox=SALES,
        conversation_id="conv-sales-packet",
        subject="Drug screen packet",
    ),
    (CR, "conv-cr-invoice"): _thread(
        thread_id=CR_THREAD_ID,
        mailbox=CR,
        conversation_id="conv-cr-invoice",
        subject="Invoice follow-up",
        urgency="NORMAL",
    ),
    (OUTSIDER, "conv-outsider-packet"): _thread(
        thread_id=OUTSIDER_THREAD_ID,
        mailbox=OUTSIDER,
        conversation_id="conv-outsider-packet",
        subject="Outsider packet",
    ),
}


def _scoped_hits(
    *,
    mailbox: str | None,
    mailboxes: list[str] | None,
) -> list[EmbeddingMatchSchema]:
    """Simulate repo mailbox filters. Unscoped (both None) leaks every row."""
    if mailbox is not None:
        allowed = {mailbox}
    elif mailboxes is not None:
        allowed = set(mailboxes)
    else:
        return list(ALL_MATCHES)
    return [hit for hit in ALL_MATCHES if hit.mailbox in allowed]


async def _fake_search_similar(_session: object, **kwargs: object) -> list[EmbeddingMatchSchema]:
    return _scoped_hits(
        mailbox=kwargs.get("mailbox"),  # type: ignore[arg-type]
        mailboxes=kwargs.get("mailboxes"),  # type: ignore[arg-type]
    )


async def _fake_search_fts(_session: object, **kwargs: object) -> list[EmbeddingMatchSchema]:
    return _scoped_hits(
        mailbox=kwargs.get("mailbox"),  # type: ignore[arg-type]
        mailboxes=kwargs.get("mailboxes"),  # type: ignore[arg-type]
    )


async def _fake_list_threads(
    _session: object,
    pairs: object,
) -> dict[tuple[str, str], ThreadSchema]:
    requested = list(pairs)  # type: ignore[arg-type]
    return {pair: THREADS[pair] for pair in requested if pair in THREADS}


@contextmanager
def _patch_retrieval():
    with (
        patch(
            "app.services.search_service.embedding_service.embed_text",
            new=AsyncMock(return_value=[0.1] * 8),
        ),
        patch(
            "app.services.search_service.embedding_repo.search_similar",
            new=AsyncMock(side_effect=_fake_search_similar),
        ),
        patch(
            "app.services.search_service.embedding_repo.search_fts",
            new=AsyncMock(side_effect=_fake_search_fts),
        ),
        patch(
            "app.services.search_service.thread_repo.list_by_mailbox_conversations",
            new=AsyncMock(side_effect=_fake_list_threads),
        ),
    ):
        yield


@pytest.mark.asyncio
async def test_blank_query_raises() -> None:
    session = AsyncMock()
    with pytest.raises(EmptySearchQueryError):
        await search_service.search_threads(
            session,
            _settings(),
            openai_client=None,
            query="   ",
        )


@pytest.mark.asyncio
async def test_empty_query_raises() -> None:
    session = AsyncMock()
    with pytest.raises(EmptySearchQueryError):
        await search_service.search_threads(
            session,
            _settings(),
            openai_client=None,
            query="",
        )


@pytest.mark.asyncio
async def test_unknown_mailbox_raises() -> None:
    session = AsyncMock()
    with pytest.raises(UnknownMailboxError):
        await search_service.search_threads(
            session,
            _settings(),
            openai_client=None,
            query="packet",
            mailbox="unknown@example.com",
        )


@pytest.mark.asyncio
async def test_mailbox_filter_returns_only_that_mailbox() -> None:
    session = AsyncMock()
    with _patch_retrieval():
        result = await search_service.search_threads(
            session,
            _settings(),
            openai_client=AsyncMock(),
            query="drug screen packet",
            mailbox="sales",
        )
    assert [hit.thread_id for hit in result.hits] == [SALES_THREAD_ID]
    assert {hit.mailbox for hit in result.hits} == {SALES}
    assert result.mailbox == SALES
    assert result.query == "drug screen packet"


@pytest.mark.asyncio
async def test_unscoped_search_excludes_mailbox_outside_allowlist() -> None:
    session = AsyncMock()
    with _patch_retrieval():
        result = await search_service.search_threads(
            session,
            _settings(),
            openai_client=AsyncMock(),
            query="drug screen packet",
        )
    thread_ids = {hit.thread_id for hit in result.hits}
    assert SALES_THREAD_ID in thread_ids
    assert CR_THREAD_ID in thread_ids
    assert OUTSIDER_THREAD_ID not in thread_ids
    assert result.mailbox is None


@pytest.mark.asyncio
async def test_happy_path_hit_fields_and_snippet() -> None:
    session = AsyncMock()
    with _patch_retrieval():
        result = await search_service.search_threads(
            session,
            _settings(),
            openai_client=AsyncMock(),
            query="packet",
            mailbox=SALES,
        )
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.thread_id == SALES_THREAD_ID
    assert hit.mailbox == SALES
    assert hit.conversation_id == "conv-sales-packet"
    assert hit.subject == "Drug screen packet"
    assert hit.state == "DRAFTED"
    assert hit.urgency == "HIGH"
    assert "Drug screen packet" in hit.snippet
    assert "drug screen packet by Friday" in hit.snippet
    assert "<" not in hit.snippet
    assert hit.score > 0
    assert hit.last_message_at == datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_ranked_hits_follow_rrf_worked_example() -> None:
    """Conv B (three hits) outranks conv A (one stronger hit). Scores from test_rrf."""
    id_a = uuid.uuid4()
    id_b1 = uuid.uuid4()
    id_b2 = uuid.uuid4()
    id_b3 = uuid.uuid4()
    vector_hits = [
        _match(
            embedding_id=uuid.uuid4(),
            conversation_id="other",
            mailbox=SALES,
            body_preview="noise",
        ),
        _match(
            embedding_id=id_a,
            conversation_id="conv-a",
            mailbox=SALES,
            body_preview="single strong hit",
        ),
        _match(
            embedding_id=uuid.uuid4(),
            conversation_id="other-2",
            mailbox=SALES,
            body_preview="noise",
        ),
        _match(
            embedding_id=id_b1,
            conversation_id="conv-b",
            mailbox=SALES,
            body_preview="corroborated thread",
        ),
        _match(
            embedding_id=uuid.uuid4(),
            conversation_id="other-3",
            mailbox=SALES,
            body_preview="noise",
        ),
        _match(
            embedding_id=id_b2,
            conversation_id="conv-b",
            mailbox=SALES,
            body_preview="corroborated thread",
        ),
        _match(
            embedding_id=uuid.uuid4(),
            conversation_id="other-4",
            mailbox=SALES,
            body_preview="noise",
        ),
        _match(
            embedding_id=uuid.uuid4(),
            conversation_id="other-5",
            mailbox=SALES,
            body_preview="noise",
        ),
        _match(
            embedding_id=id_b3,
            conversation_id="conv-b",
            mailbox=SALES,
            body_preview="corroborated thread",
        ),
    ]
    threads = {
        (SALES, "conv-a"): _thread(
            thread_id=CONV_A_THREAD_ID,
            mailbox=SALES,
            conversation_id="conv-a",
            subject="Single hit thread",
        ),
        (SALES, "conv-b"): _thread(
            thread_id=CONV_B_THREAD_ID,
            mailbox=SALES,
            conversation_id="conv-b",
            subject="Corroborated thread",
        ),
    }

    async def fake_vector(_session: object, **kwargs: object) -> list[EmbeddingMatchSchema]:
        return vector_hits

    async def fake_fts(_session: object, **kwargs: object) -> list[EmbeddingMatchSchema]:
        return []

    async def fake_threads(_session: object, pairs: object) -> dict[tuple[str, str], ThreadSchema]:
        return {pair: threads[pair] for pair in pairs if pair in threads}  # type: ignore[misc]

    session = AsyncMock()
    with (
        patch(
            "app.services.search_service.embedding_service.embed_text",
            new=AsyncMock(return_value=[0.1] * 8),
        ),
        patch(
            "app.services.search_service.embedding_repo.search_similar",
            new=AsyncMock(side_effect=fake_vector),
        ),
        patch(
            "app.services.search_service.embedding_repo.search_fts",
            new=AsyncMock(side_effect=fake_fts),
        ),
        patch(
            "app.services.search_service.thread_repo.list_by_mailbox_conversations",
            new=AsyncMock(side_effect=fake_threads),
        ),
    ):
        result = await search_service.search_threads(
            session,
            _settings(),
            openai_client=AsyncMock(),
            query="packet",
            mailbox=SALES,
            limit=2,
        )

    assert [hit.thread_id for hit in result.hits] == [CONV_B_THREAD_ID, CONV_A_THREAD_ID]
    # Independently known from test_rrf worked example (not recomputed here).
    assert result.hits[0].score == pytest.approx(0.0203125)
    assert result.hits[1].score == pytest.approx(0.016129, abs=1e-6)


@pytest.mark.asyncio
async def test_embed_failure_falls_back_to_fts() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.search_service.embedding_service.embed_text",
            new=AsyncMock(side_effect=RuntimeError("openai down")),
        ),
        patch(
            "app.services.search_service.embedding_repo.search_similar",
            new=AsyncMock(side_effect=AssertionError("vector leg must not run")),
        ),
        patch(
            "app.services.search_service.embedding_repo.search_fts",
            new=AsyncMock(side_effect=_fake_search_fts),
        ),
        patch(
            "app.services.search_service.thread_repo.list_by_mailbox_conversations",
            new=AsyncMock(side_effect=_fake_list_threads),
        ),
    ):
        result = await search_service.search_threads(
            session,
            _settings(),
            openai_client=AsyncMock(),
            query="packet",
            mailbox=SALES,
        )
    assert [hit.thread_id for hit in result.hits] == [SALES_THREAD_ID]


@pytest.mark.asyncio
async def test_missing_openai_client_uses_fts_only() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.search_service.embedding_service.embed_text",
            new=AsyncMock(side_effect=AssertionError("embed must not run")),
        ),
        patch(
            "app.services.search_service.embedding_repo.search_similar",
            new=AsyncMock(side_effect=AssertionError("vector leg must not run")),
        ),
        patch(
            "app.services.search_service.embedding_repo.search_fts",
            new=AsyncMock(side_effect=_fake_search_fts),
        ),
        patch(
            "app.services.search_service.thread_repo.list_by_mailbox_conversations",
            new=AsyncMock(side_effect=_fake_list_threads),
        ),
    ):
        result = await search_service.search_threads(
            session,
            _settings(),
            openai_client=None,
            query="packet",
            mailbox=SALES,
        )
    assert [hit.thread_id for hit in result.hits] == [SALES_THREAD_ID]


@pytest.mark.asyncio
async def test_empty_allowlist_returns_no_hits_without_searching() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.search_service.embedding_repo.search_similar",
            new=AsyncMock(side_effect=AssertionError("must not query")),
        ),
        patch(
            "app.services.search_service.embedding_repo.search_fts",
            new=AsyncMock(side_effect=AssertionError("must not query")),
        ),
    ):
        result = await search_service.search_threads(
            session,
            _settings(target_mailboxes=""),
            openai_client=None,
            query="packet",
        )
    assert result.hits == []


def test_snippet_joins_subject_and_body() -> None:
    snippet = build_snippet(
        subject="Drug screen packet",
        body="Please send the drug screen packet by Friday.",
    )
    assert snippet == "Drug screen packet — Please send the drug screen packet by Friday."


def test_snippet_truncates_to_240_chars() -> None:
    body = "x" * 400
    snippet = build_snippet(subject="Hi", body=body)
    assert len(snippet) == SEARCH_SNIPPET_MAX_CHARS
    assert snippet.startswith("Hi — ")
    assert snippet.endswith("...")


def test_snippet_strips_html_tags() -> None:
    snippet = build_snippet(
        subject="Hello",
        body="<p>Please send the <b>packet</b> today.</p>",
    )
    assert "<" not in snippet
    assert "packet" in snippet
    assert snippet.startswith("Hello — ")


def _embedding_dim() -> int:
    return 1536


async def _seed_search_rows(session: object) -> dict[str, uuid.UUID]:
    from app.models.db.email_embedding import EmailEmbedding
    from app.models.db.message import Message
    from app.models.db.thread import Thread

    now = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    sales_id = uuid.uuid4()
    cr_id = uuid.uuid4()
    outsider_id = uuid.uuid4()
    sales_thread = Thread(
        id=sales_id,
        mailbox=SALES,
        conversation_id="conv-sales-packet",
        subject="Drug screen packet",
        state="DRAFTED",
        urgency="HIGH",
        last_message_at=now,
    )
    cr_thread = Thread(
        id=cr_id,
        mailbox=CR,
        conversation_id="conv-cr-invoice",
        subject="Invoice follow-up",
        state="DRAFTED",
        urgency="NORMAL",
        last_message_at=now,
    )
    outsider_thread = Thread(
        id=outsider_id,
        mailbox=OUTSIDER,
        conversation_id="conv-outsider-packet",
        subject="Outsider packet",
        state="DRAFTED",
        urgency="HIGH",
        last_message_at=now,
    )
    session.add_all([sales_thread, cr_thread, outsider_thread])  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]

    sales_msg = Message(
        id=uuid.uuid4(),
        thread_id=sales_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="vendor@example.com",
        body_text="Please send the drug screen packet by Friday.",
        body_preview="Please send the drug screen packet by Friday.",
        received_at=now,
        to_recipients=[SALES],
        cc_recipients=[],
    )
    cr_msg = Message(
        id=uuid.uuid4(),
        thread_id=cr_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="client@example.com",
        body_text="Following up on the unpaid invoice.",
        body_preview="Following up on the unpaid invoice.",
        received_at=now,
        to_recipients=[CR],
        cc_recipients=[],
    )
    outsider_msg = Message(
        id=uuid.uuid4(),
        thread_id=outsider_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="vendor@example.com",
        body_text="Secret drug screen packet for another tenant.",
        body_preview="Secret drug screen packet for another tenant.",
        received_at=now,
        to_recipients=[OUTSIDER],
        cc_recipients=[],
    )
    session.add_all([sales_msg, cr_msg, outsider_msg])  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]

    zeros = [0.0] * _embedding_dim()
    session.add_all(  # type: ignore[union-attr]
        [
            EmailEmbedding(
                mailbox=SALES,
                conversation_id="conv-sales-packet",
                sender_email="vendor@example.com",
                recipient_emails=[SALES],
                cc_emails=[],
                embedding=zeros,
                sent_at=now,
                body_preview="Please send the drug screen packet by Friday.",
                search_document=(
                    "From: vendor@example.com | Subject: Drug screen packet\n\n"
                    "Please send the drug screen packet by Friday."
                ),
                message_id=sales_msg.id,
            ),
            EmailEmbedding(
                mailbox=CR,
                conversation_id="conv-cr-invoice",
                sender_email="client@example.com",
                recipient_emails=[CR],
                cc_emails=[],
                embedding=zeros,
                sent_at=now,
                body_preview="Following up on the unpaid invoice.",
                search_document=(
                    "From: client@example.com | Subject: Invoice follow-up\n\n"
                    "Following up on the unpaid invoice."
                ),
                message_id=cr_msg.id,
            ),
            EmailEmbedding(
                mailbox=OUTSIDER,
                conversation_id="conv-outsider-packet",
                sender_email="vendor@example.com",
                recipient_emails=[OUTSIDER],
                cc_emails=[],
                embedding=zeros,
                sent_at=now,
                body_preview="Secret drug screen packet for another tenant.",
                search_document=(
                    "From: vendor@example.com | Subject: Outsider packet\n\n"
                    "Secret drug screen packet for another tenant."
                ),
                message_id=outsider_msg.id,
            ),
        ]
    )
    await session.commit()  # type: ignore[union-attr]
    return {"sales": sales_id, "cr": cr_id, "outsider": outsider_id}


@pytest.mark.db
@pytest.mark.asyncio
async def test_fts_search_does_not_leak_across_mailboxes(db_session) -> None:
    ids = await _seed_search_rows(db_session)
    settings = _settings()

    sales_only = await search_service.search_threads(
        db_session,
        settings,
        openai_client=None,
        query="drug screen packet",
        mailbox="sales",
    )
    assert [hit.thread_id for hit in sales_only.hits] == [ids["sales"]]
    assert {hit.mailbox for hit in sales_only.hits} == {SALES}

    unscoped = await search_service.search_threads(
        db_session,
        settings,
        openai_client=None,
        query="drug screen packet",
    )
    unscoped_ids = {hit.thread_id for hit in unscoped.hits}
    assert ids["sales"] in unscoped_ids
    assert ids["outsider"] not in unscoped_ids
    assert ids["cr"] not in unscoped_ids

    cr_only = await search_service.search_threads(
        db_session,
        settings,
        openai_client=None,
        query="unpaid invoice",
        mailbox=CR,
    )
    assert [hit.thread_id for hit in cr_only.hits] == [ids["cr"]]
