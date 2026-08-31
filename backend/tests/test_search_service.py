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
    sender_email: str | None = "vendor@example.com",
) -> EmbeddingMatchSchema:
    return EmbeddingMatchSchema(
        id=embedding_id,
        conversation_id=conversation_id,
        similarity_score=score,
        message_id=message_id or uuid.uuid4(),
        mailbox=mailbox,
        body_preview=body_preview,
        sender_email=sender_email,
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
        patch(
            "app.services.search_service.thread_repo.search_keyword_threads",
            new=AsyncMock(return_value=[]),
        ),
    ):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   "])
async def test_empty_or_blank_query_raises(query: str) -> None:
    session = AsyncMock()
    with pytest.raises(EmptySearchQueryError):
        await search_service.search_threads(
            session,
            _settings(),
            openai_client=None,
            query=query,
        )


@pytest.mark.asyncio
async def test_dangling_from_filter_raises_like_a_blank_query() -> None:
    session = AsyncMock()
    with pytest.raises(EmptySearchQueryError):
        await search_service.search_threads(
            session,
            _settings(),
            openai_client=None,
            query="from:",
        )


@pytest.mark.asyncio
async def test_filler_only_chat_ask_raises_without_allow_empty() -> None:
    """'What should I focus on today?' is not a keyword. Search API stays blank."""
    session = AsyncMock()
    with pytest.raises(EmptySearchQueryError):
        await search_service.search_threads(
            session,
            _settings(),
            openai_client=None,
            query="what should I focus on today?",
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
async def test_nl_mailbox_outside_request_scope_raises() -> None:
    """mailbox:CR while the request is scoped to sales is not 'zero hits'."""
    session = AsyncMock()
    with pytest.raises(UnknownMailboxError, match="Mailbox not found"):
        await search_service.search_threads(
            session,
            _settings(),
            openai_client=None,
            query=f"mailbox:{CR} packet",
            mailbox=SALES,
        )


def test_contentful_tokens_rejects_filler_and_keeps_keywords() -> None:
    from app.services.search_service import contentful_tokens, retrieval_tokens

    assert contentful_tokens("what should I focus on today?") is False
    assert contentful_tokens("Ashley Cantrell invoice") is True
    assert "Ashley" in retrieval_tokens("Ashley Cantrell invoice")


@pytest.mark.asyncio
async def test_keyword_mode_returns_fts_hits_not_vector_only_matches() -> None:
    """Keyword search is Outlook-style FTS. A vector-only CR hit must not appear."""

    async def fake_vector(_session: object, **kwargs: object) -> list[EmbeddingMatchSchema]:
        return [CR_MATCH]

    async def fake_fts(_session: object, **kwargs: object) -> list[EmbeddingMatchSchema]:
        return [SALES_MATCH]

    embed = AsyncMock(return_value=[0.1] * 8)
    similar = AsyncMock(side_effect=fake_vector)
    session = AsyncMock()
    with (
        patch("app.services.search_service.embedding_service.embed_text", new=embed),
        patch("app.services.search_service.embedding_repo.search_similar", new=similar),
        patch(
            "app.services.search_service.embedding_repo.search_fts",
            new=AsyncMock(side_effect=fake_fts),
        ),
        patch(
            "app.services.search_service.thread_repo.list_by_mailbox_conversations",
            new=AsyncMock(side_effect=_fake_list_threads),
        ),
        patch(
            "app.services.search_service.thread_repo.search_keyword_threads",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await search_service.search_threads(
            session,
            _settings(),
            openai_client=AsyncMock(),
            query="drug screen packet",
            mode="keyword",
        )

    assert [hit.thread_id for hit in result.hits] == [SALES_THREAD_ID]
    assert result.hits[0].subject == "Drug screen packet"
    assert result.hits[0].sender == "vendor@example.com"
    assert embed.await_count == 0
    assert similar.await_count == 0


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
        patch(
            "app.services.search_service.thread_repo.search_keyword_threads",
            new=AsyncMock(return_value=[]),
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
        patch(
            "app.services.search_service.thread_repo.search_keyword_threads",
            new=AsyncMock(return_value=[]),
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
        patch(
            "app.services.search_service.thread_repo.search_keyword_threads",
            new=AsyncMock(return_value=[]),
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


def test_snippet_is_body_only_without_subject_prefix() -> None:
    """UI already shows the subject — snippet is the body window only."""
    snippet = build_snippet(
        body="Please send the drug screen packet by Friday.",
    )
    assert snippet == "Please send the drug screen packet by Friday."
    assert "—" not in snippet


def test_snippet_centers_on_highlight_term() -> None:
    """Match-centered window so deep hits are visible, not only the email opening."""
    prefix = "Opening pleasantries and more filler words. " * 12
    body = f"{prefix}Please remit unpaid invoice 9921 this week. Thanks again."
    assert len(body) > SEARCH_SNIPPET_MAX_CHARS
    snippet = build_snippet(body=body, highlight="unpaid invoice")
    assert "unpaid invoice 9921" in snippet
    assert not snippet.startswith("Opening pleasantries")
    assert len(snippet) <= SEARCH_SNIPPET_MAX_CHARS + 6  # optional leading/trailing ...


def test_snippet_truncates_to_240_chars() -> None:
    body = "x" * 400
    snippet = build_snippet(body=body)
    assert len(snippet) == SEARCH_SNIPPET_MAX_CHARS
    assert snippet.endswith("...")


def test_snippet_strips_html_tags() -> None:
    snippet = build_snippet(
        body="<p>Please send the <b>packet</b> today.</p>",
    )
    assert "<" not in snippet
    assert "packet" in snippet
    assert "Please send the packet today" in snippet


def test_snippet_strips_quoted_history_before_window() -> None:
    quoted = "\n".join(f"> invoice 1 line {i}" for i in range(50))
    body = f"New reply about invoice 42\n\nOn Thu, Aug 14, 2026 at 3:00 PM Alice wrote:\n{quoted}\n"
    snippet = build_snippet(body=body, highlight="invoice")
    assert "invoice 42" in snippet
    assert "invoice 1" not in snippet


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


def test_fts_query_quotes_proper_names_and_drops_chat_filler() -> None:
    from app.services.search_service import build_fts_query

    rewritten = build_fts_query("What's happening on Ashley Cantrell, give me the latest")
    assert rewritten == '"Ashley Cantrell"'


def test_fts_query_keeps_keywords_beside_quoted_names() -> None:
    """A name plus a real keyword must keep both — invoice must not be dropped."""
    from app.services.search_service import build_fts_query

    rewritten = build_fts_query("Ashley Cantrell invoice")
    assert rewritten == '"Ashley Cantrell" invoice'


def test_fts_query_keeps_keyword_asks_unchanged() -> None:
    from app.services.search_service import build_fts_query

    assert build_fts_query("drug screen packet") == "drug screen packet"
    assert build_fts_query("billing disputes waiting on review") == (
        "billing disputes waiting on review"
    )


def test_prefix_tsquery_drops_chat_filler_and_keeps_the_entity() -> None:
    from app.services.search_service import build_prefix_tsquery

    q = build_prefix_tsquery(
        "give me the latest on info what s happening there what do i need to focus on first"
    )
    assert q == "info:*"
    assert "give" not in q
    assert "happening" not in q
    assert "focus" not in q


def test_prefix_tsquery_drops_thread_meta_nouns() -> None:
    """Chat phrasing must not AND 'threads'/'emails' with the topic token."""
    from app.services.search_service import build_prefix_tsquery

    assert build_prefix_tsquery("Threads about SampleLab") == "SampleLab:*"
    assert build_prefix_tsquery("emails from invoices") == "invoices:*"


def test_content_embed_text_strips_chat_filler() -> None:
    """Vector search must embed the company/topic, not 'give me the latest'."""
    from app.services.search_service import content_embed_text

    assert (
        content_embed_text(
            "can you give me the latest on Omason timber products? what s happening with them?"
        )
        == "Omason timber products"
    )


def test_prefix_tsquery_does_not_require_lowercase_paraphrase_tokens() -> None:
    """Named entity is required; 'timber products' must not AND-fail a lumber thread."""
    from app.services.search_service import build_fts_query, build_prefix_tsquery

    assert build_prefix_tsquery(build_fts_query("Omason timber products")) == "Omason:*"


def test_prefix_tsquery_title_case_company_keeps_the_name_only() -> None:
    """A 3-word Title Case company is not First Last; AND-ing Timber misses lumber mail."""
    from app.services.search_service import build_fts_query, build_prefix_tsquery

    assert build_prefix_tsquery(build_fts_query("Omason Timber Products")) == "Omason:*"


@pytest.mark.db
@pytest.mark.asyncio
async def test_conversational_person_ask_finds_named_thread(db_session) -> None:
    """Chatty 'what's happening on NAME' must not AND filler tokens like happen/give/latest."""
    from app.models.db.email_embedding import EmailEmbedding
    from app.models.db.message import Message
    from app.models.db.thread import Thread

    now = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    thread_id = uuid.uuid4()
    thread = Thread(
        id=thread_id,
        mailbox=SALES,
        conversation_id="conv-ashley-background",
        subject="Background check inquiry",
        state="DRAFTED",
        urgency="NORMAL",
        last_message_at=now,
    )
    db_session.add(thread)
    await db_session.flush()
    message = Message(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="alex@sample-legal.example.com",
        body_text=(
            "A colleague of mine, Ashley Cantrell with Stronger Together, "
            "asked about the background check."
        ),
        body_preview="A colleague of mine, Ashley Cantrell with Stronger Together.",
        received_at=now,
        to_recipients=[SALES],
        cc_recipients=[],
    )
    db_session.add(message)
    await db_session.flush()
    db_session.add(
        EmailEmbedding(
            mailbox=SALES,
            conversation_id="conv-ashley-background",
            sender_email="alex@sample-legal.example.com",
            recipient_emails=[SALES],
            cc_emails=[],
            embedding=[0.0] * _embedding_dim(),
            sent_at=now,
            body_preview="A colleague of mine, Ashley Cantrell with Stronger Together.",
            search_document=(
                "From: alex@sample-legal.example.com | Subject: Background check inquiry\n\n"
                "A colleague of mine, Ashley Cantrell with Stronger Together, "
                "asked about the background check."
            ),
            message_id=message.id,
        )
    )
    await db_session.commit()

    result = await search_service.search_threads(
        db_session,
        _settings(),
        openai_client=None,
        query="What's happening on Ashley Cantrell, give me the latest",
    )
    assert [hit.thread_id for hit in result.hits] == [thread_id]
    assert "Ashley Cantrell" in result.hits[0].snippet


@pytest.mark.db
@pytest.mark.asyncio
async def test_chatty_overview_ask_finds_info_thread(db_session) -> None:
    """'Latest on info / what should I focus on' must not AND filler tokens."""
    from app.models.db.email_embedding import EmailEmbedding
    from app.models.db.message import Message
    from app.models.db.thread import Thread

    now = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    thread_id = uuid.uuid4()
    thread = Thread(
        id=thread_id,
        mailbox=SALES,
        conversation_id="conv-info-order",
        subject="Info background order",
        state="DRAFTED",
        urgency="HIGH",
        last_message_at=now,
    )
    db_session.add(thread)
    await db_session.flush()
    message = Message(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="orders@info.test",
        body_text="Please process the Info background order by Friday.",
        body_preview="Please process the Info background order by Friday.",
        received_at=now,
        to_recipients=[SALES],
        cc_recipients=[],
    )
    db_session.add(message)
    await db_session.flush()
    db_session.add(
        EmailEmbedding(
            mailbox=SALES,
            conversation_id="conv-info-order",
            sender_email="orders@info.test",
            recipient_emails=[SALES],
            cc_emails=[],
            embedding=[0.0] * _embedding_dim(),
            sent_at=now,
            body_preview="Please process the Info background order by Friday.",
            search_document=(
                "From: orders@info.test | Subject: Info background order\n\n"
                "Please process the Info background order by Friday."
            ),
            message_id=message.id,
        )
    )
    await db_session.commit()

    result = await search_service.search_threads(
        db_session,
        _settings(),
        openai_client=None,
        query=(
            "give me the latest on info. what s happening there? what do i need to focus on first?"
        ),
        mode="keyword",
    )
    assert [hit.thread_id for hit in result.hits] == [thread_id]
    assert result.hits[0].subject == "Info background order"


async def _seed_samplehelpdesk_thread(session: object) -> uuid.UUID:
    from app.models.db.email_embedding import EmailEmbedding
    from app.models.db.message import Message
    from app.models.db.thread import Thread

    now = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    thread_id = uuid.uuid4()
    session.add(  # type: ignore[union-attr]
        Thread(
            id=thread_id,
            mailbox=SALES,
            conversation_id="conv-samplehelpdesk-order",
            subject="SampleHelpdesk order",
            state="DRAFTED",
            urgency="NORMAL",
            last_message_at=now,
        )
    )
    await session.flush()  # type: ignore[union-attr]
    message = Message(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="orders@sample-helpdesk.test",
        body_text="Please process the SampleHelpdesk background order.",
        body_preview="Please process the SampleHelpdesk background order.",
        received_at=now,
        to_recipients=[SALES],
        cc_recipients=[],
    )
    session.add(message)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]
    session.add(  # type: ignore[union-attr]
        EmailEmbedding(
            mailbox=SALES,
            conversation_id="conv-samplehelpdesk-order",
            sender_email="orders@sample-helpdesk.test",
            recipient_emails=[SALES],
            cc_emails=[],
            embedding=[0.0] * _embedding_dim(),
            sent_at=now,
            body_preview="Please process the SampleHelpdesk background order.",
            search_document=(
                "From: orders@sample-helpdesk.test | Subject: SampleHelpdesk order\n\n"
                "Please process the SampleHelpdesk background order."
            ),
            message_id=message.id,
        )
    )
    await session.commit()  # type: ignore[union-attr]
    return thread_id


@pytest.mark.db
@pytest.mark.asyncio
async def test_keyword_prefix_finds_compound_name(db_session) -> None:
    """Outlook-style search: SampleH must hit a thread tokenized as SampleHelpdesk."""
    thread_id = await _seed_samplehelpdesk_thread(db_session)

    prefix = await search_service.search_threads(
        db_session,
        _settings(),
        openai_client=None,
        query="SampleH",
        mode="keyword",
    )
    assert [hit.thread_id for hit in prefix.hits] == [thread_id]
    assert prefix.hits[0].subject == "SampleHelpdesk order"

    full = await search_service.search_threads(
        db_session,
        _settings(),
        openai_client=None,
        query="SampleHelpdesk",
        mode="keyword",
    )
    assert [hit.thread_id for hit in full.hits] == [thread_id]


@pytest.mark.db
@pytest.mark.asyncio
async def test_keyword_query_strips_html_and_fts_operators(db_session) -> None:
    """Tags and FTS operators cannot 500, echo markup, or hide a prefix match."""
    thread_id = await _seed_samplehelpdesk_thread(db_session)

    result = await search_service.search_threads(
        db_session,
        _settings(),
        openai_client=None,
        query="<script>SampleH</script> & !()",
        mode="keyword",
    )
    assert result.query == "SampleH"
    assert "<" not in result.query
    assert "script" not in result.query.lower()
    assert [hit.thread_id for hit in result.hits] == [thread_id]


async def _seed_filter_search_rows(session: object) -> dict[str, uuid.UUID]:
    """Three threads so stacked filters can be counted by hand.

    1. sales inbound from vendor — subject packet, body 'Please send the packet'
    2. sales outbound from sales — same subject, body 'We sent the packet'
    3. cr inbound from vendor — subject invoice, body 'unpaid invoice'
    """
    from app.models.db.email_embedding import EmailEmbedding
    from app.models.db.message import Message
    from app.models.db.thread import Thread

    now = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    sales_in_id = uuid.uuid4()
    sales_out_id = uuid.uuid4()
    cr_id = uuid.uuid4()
    sales_in = Thread(
        id=sales_in_id,
        mailbox=SALES,
        conversation_id="conv-sales-in-packet",
        subject="Drug screen packet",
        state="DRAFTED",
        urgency="HIGH",
        last_message_at=now,
    )
    sales_out = Thread(
        id=sales_out_id,
        mailbox=SALES,
        conversation_id="conv-sales-out-packet",
        subject="Drug screen packet",
        state="DRAFTED",
        urgency="NORMAL",
        last_message_at=now,
    )
    cr = Thread(
        id=cr_id,
        mailbox=CR,
        conversation_id="conv-cr-unpaid",
        subject="Invoice follow-up",
        state="DRAFTED",
        urgency="HIGH",
        last_message_at=now,
    )
    session.add_all([sales_in, sales_out, cr])  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]

    sales_in_msg = Message(
        id=uuid.uuid4(),
        thread_id=sales_in_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="vendor@example.com",
        body_text="Please send the packet",
        body_preview="Please send the packet",
        received_at=now,
        to_recipients=[SALES],
        cc_recipients=[],
    )
    sales_out_msg = Message(
        id=uuid.uuid4(),
        thread_id=sales_out_id,
        graph_message_id=str(uuid.uuid4()),
        direction="outbound",
        sender=SALES,
        body_text="We sent the packet",
        body_preview="We sent the packet",
        received_at=now,
        to_recipients=["vendor@example.com"],
        cc_recipients=[],
    )
    cr_msg = Message(
        id=uuid.uuid4(),
        thread_id=cr_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="vendor@example.com",
        body_text="Following up on the unpaid invoice.",
        body_preview="Following up on the unpaid invoice.",
        received_at=now,
        to_recipients=[CR],
        cc_recipients=[],
    )
    session.add_all([sales_in_msg, sales_out_msg, cr_msg])  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]

    zeros = [0.0] * _embedding_dim()
    session.add_all(  # type: ignore[union-attr]
        [
            EmailEmbedding(
                mailbox=SALES,
                conversation_id="conv-sales-in-packet",
                sender_email="vendor@example.com",
                recipient_emails=[SALES],
                cc_emails=[],
                embedding=zeros,
                sent_at=now,
                body_preview="Please send the packet",
                search_document=(
                    "From: vendor@example.com | Subject: Drug screen packet\n\n"
                    "Please send the packet"
                ),
                message_id=sales_in_msg.id,
            ),
            EmailEmbedding(
                mailbox=SALES,
                conversation_id="conv-sales-out-packet",
                sender_email=SALES,
                recipient_emails=["vendor@example.com"],
                cc_emails=[],
                embedding=zeros,
                sent_at=now,
                body_preview="We sent the packet",
                search_document=(
                    f"From: {SALES} | Subject: Drug screen packet\n\nWe sent the packet"
                ),
                message_id=sales_out_msg.id,
            ),
            EmailEmbedding(
                mailbox=CR,
                conversation_id="conv-cr-unpaid",
                sender_email="vendor@example.com",
                recipient_emails=[CR],
                cc_emails=[],
                embedding=zeros,
                sent_at=now,
                body_preview="Following up on the unpaid invoice.",
                search_document=(
                    "From: vendor@example.com | Subject: Invoice follow-up\n\n"
                    "Following up on the unpaid invoice."
                ),
                message_id=cr_msg.id,
            ),
        ]
    )
    await session.commit()  # type: ignore[union-attr]
    return {"sales_in": sales_in_id, "sales_out": sales_out_id, "cr": cr_id}


@pytest.mark.db
@pytest.mark.asyncio
async def test_stacked_discord_filters_narrow_to_one_hand_counted_thread(
    db_session,
) -> None:
    ids = await _seed_filter_search_rows(db_session)
    settings = _settings()

    stacked = await search_service.search_threads(
        db_session,
        settings,
        openai_client=None,
        query="from:vendor@example.com subject:packet mailbox:sales direction:inbound",
        mode="keyword",
    )
    assert [hit.thread_id for hit in stacked.hits] == [ids["sales_in"]]

    outbound = await search_service.search_threads(
        db_session,
        settings,
        openai_client=None,
        query="direction:outbound mailbox:sales",
        mode="keyword",
    )
    assert [hit.thread_id for hit in outbound.hits] == [ids["sales_out"]]

    unpaid = await search_service.search_threads(
        db_session,
        settings,
        openai_client=None,
        query="contains:unpaid",
        mode="keyword",
    )
    assert [hit.thread_id for hit in unpaid.hits] == [ids["cr"]]

    spaced = await search_service.search_threads(
        db_session,
        settings,
        openai_client=None,
        query="from: vendor@example.com",
        mode="keyword",
    )
    spaced_ids = [hit.thread_id for hit in spaced.hits]
    assert ids["sales_in"] in spaced_ids
    assert ids["cr"] in spaced_ids
    assert ids["sales_out"] not in spaced_ids


@pytest.mark.db
@pytest.mark.asyncio
async def test_contains_matches_deep_search_document_not_only_preview(
    db_session,
) -> None:
    """contains: must find terms past the truncated body_preview window."""
    from app.models.db.email_embedding import EmailEmbedding
    from app.models.db.message import Message
    from app.models.db.thread import Thread

    now = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    thread_id = uuid.uuid4()
    thread = Thread(
        id=thread_id,
        mailbox=SALES,
        conversation_id="conv-deep-contains",
        subject="Long follow-up",
        state="DRAFTED",
        urgency="NORMAL",
        last_message_at=now,
    )
    db_session.add(thread)
    await db_session.flush()
    message = Message(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="vendor@example.com",
        body_text="Please see attached.",
        body_preview="Please see attached.",
        received_at=now,
        to_recipients=[SALES],
        cc_recipients=[],
    )
    db_session.add(message)
    await db_session.flush()
    zeros = [0.0] * _embedding_dim()
    deep_token = "ZZDEEPCONTAINS991"
    db_session.add(
        EmailEmbedding(
            mailbox=SALES,
            conversation_id="conv-deep-contains",
            sender_email="vendor@example.com",
            recipient_emails=[SALES],
            cc_emails=[],
            embedding=zeros,
            sent_at=now,
            body_preview="Please see attached.",
            search_document=(
                "From: vendor@example.com | Subject: Long follow-up\n\n"
                f"{'filler ' * 40}{deep_token} at the end."
            ),
            message_id=message.id,
        )
    )
    await db_session.commit()

    result = await search_service.search_threads(
        db_session,
        _settings(),
        openai_client=None,
        query=f"contains:{deep_token}",
        mode="keyword",
    )
    assert [hit.thread_id for hit in result.hits] == [thread_id]


@pytest.mark.db
@pytest.mark.asyncio
async def test_mailbox_only_filter_lists_that_mailbox_not_others(
    db_session,
) -> None:
    """Discord ``mailbox:sales`` alone is a valid search — both sales threads, not CR.

    Hand count from fixture: sales inbound + sales outbound = 2; CR unpaid stays out.
    """
    ids = await _seed_filter_search_rows(db_session)

    result = await search_service.search_threads(
        db_session,
        _settings(),
        openai_client=None,
        query="mailbox:sales",
        mode="keyword",
    )
    hit_ids = {hit.thread_id for hit in result.hits}
    assert hit_ids == {ids["sales_in"], ids["sales_out"]}
    assert ids["cr"] not in hit_ids


INFO = "info@sample-services.example.com"


async def _seed_info_and_sales_mailboxes(session: object) -> dict[str, uuid.UUID]:
    """Worked example: info@ has one thread; sales has an unrelated packet thread.

    Hand count: NL 'info mailbox' must return only the info thread (1), never sales.
    Bodies intentionally omit the word 'mailbox' so keyword AND would fail.
    """
    from app.models.db.email_embedding import EmailEmbedding
    from app.models.db.message import Message
    from app.models.db.thread import Thread

    now = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    info_id = uuid.uuid4()
    sales_id = uuid.uuid4()
    session.add(  # type: ignore[union-attr]
        Thread(
            id=info_id,
            mailbox=INFO,
            conversation_id="conv-info-latest",
            subject="Background order confirmation",
            state="DRAFTED",
            urgency="HIGH",
            last_message_at=now,
        )
    )
    session.add(  # type: ignore[union-attr]
        Thread(
            id=sales_id,
            mailbox=SALES,
            conversation_id="conv-sales-unrelated",
            subject="Drug screen packet",
            state="DRAFTED",
            urgency="NORMAL",
            last_message_at=now,
        )
    )
    await session.flush()  # type: ignore[union-attr]

    info_msg = Message(
        id=uuid.uuid4(),
        thread_id=info_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="client@example.com",
        body_text="Please confirm the background order for Friday.",
        body_preview="Please confirm the background order for Friday.",
        received_at=now,
        to_recipients=[INFO],
        cc_recipients=[],
    )
    sales_msg = Message(
        id=uuid.uuid4(),
        thread_id=sales_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="buyer@example.com",
        body_text="Please send the drug screen packet.",
        body_preview="Please send the drug screen packet.",
        received_at=now,
        to_recipients=[SALES],
        cc_recipients=[],
    )
    session.add(info_msg)  # type: ignore[union-attr]
    session.add(sales_msg)  # type: ignore[union-attr]
    await session.flush()  # type: ignore[union-attr]

    dim = _embedding_dim()
    session.add(  # type: ignore[union-attr]
        EmailEmbedding(
            mailbox=INFO,
            conversation_id="conv-info-latest",
            sender_email="client@example.com",
            recipient_emails=[INFO],
            cc_emails=[],
            embedding=[0.0] * dim,
            sent_at=now,
            body_preview="Please confirm the background order for Friday.",
            search_document=(
                "From: client@example.com | Subject: Background order confirmation\n\n"
                "Please confirm the background order for Friday."
            ),
            message_id=info_msg.id,
        )
    )
    session.add(  # type: ignore[union-attr]
        EmailEmbedding(
            mailbox=SALES,
            conversation_id="conv-sales-unrelated",
            sender_email="buyer@example.com",
            recipient_emails=[SALES],
            cc_emails=[],
            embedding=[0.0] * dim,
            sent_at=now,
            body_preview="Please send the drug screen packet.",
            search_document=(
                "From: buyer@example.com | Subject: Drug screen packet\n\n"
                "Please send the drug screen packet."
            ),
            message_id=sales_msg.id,
        )
    )
    await session.commit()  # type: ignore[union-attr]
    return {"info": info_id, "sales": sales_id}


@pytest.mark.db
@pytest.mark.asyncio
async def test_nl_info_mailbox_ask_lists_info_threads_not_sales(
    db_session,
) -> None:
    """'latest on the info mailbox' scopes to info@; bodies have no word 'mailbox'."""
    ids = await _seed_info_and_sales_mailboxes(db_session)
    settings = _settings(target_mailboxes=f"{SALES},{CR},{INFO}")

    result = await search_service.search_threads(
        db_session,
        settings,
        openai_client=None,
        query="what s the latest on the info mailbox",
        mode="keyword",
    )
    assert [hit.thread_id for hit in result.hits] == [ids["info"]]
    assert result.hits[0].mailbox == INFO
    assert result.mailbox == INFO
    assert ids["sales"] not in {hit.thread_id for hit in result.hits}


@pytest.mark.db
@pytest.mark.asyncio
async def test_nl_info_mailbox_with_topic_keeps_content_keywords(
    db_session,
) -> None:
    """Scope to info@ but still require the topic keyword in that mailbox."""
    ids = await _seed_info_and_sales_mailboxes(db_session)
    settings = _settings(target_mailboxes=f"{SALES},{CR},{INFO}")

    miss = await search_service.search_threads(
        db_session,
        settings,
        openai_client=None,
        query="info mailbox drug screen packet",
        mode="keyword",
    )
    assert miss.hits == []

    hit = await search_service.search_threads(
        db_session,
        settings,
        openai_client=None,
        query="info mailbox background order",
        mode="keyword",
    )
    assert [h.thread_id for h in hit.hits] == [ids["info"]]


@pytest.mark.db
@pytest.mark.asyncio
async def test_filler_only_with_allow_empty_lists_recent_allowlist_threads(
    db_session,
) -> None:
    """Chat overview: filler-only + allow_empty lists info and sales, not outsider CR."""
    ids = await _seed_info_and_sales_mailboxes(db_session)
    settings = _settings(target_mailboxes=f"{SALES},{INFO}")

    result = await search_service.search_threads(
        db_session,
        settings,
        openai_client=None,
        query="what should I focus on today?",
        allow_empty=True,
        mode="keyword",
    )
    hit_ids = {hit.thread_id for hit in result.hits}
    assert hit_ids == {ids["info"], ids["sales"]}


@pytest.mark.asyncio
async def test_hybrid_embeds_content_tokens_not_chat_filler() -> None:
    """Chatty asks must not be embedded whole — filler pulls the vector off the mail."""
    captured: dict[str, str] = {}

    async def embed(text: str, **_kwargs: object) -> list[float]:
        captured["text"] = text
        return [0.1] * 8

    session = AsyncMock()
    with (
        patch("app.services.search_service.embedding_service.embed_text", new=embed),
        patch(
            "app.services.search_service.embedding_repo.search_similar",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.search_service.embedding_repo.search_fts",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.search_service.thread_repo.list_by_mailbox_conversations",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.services.search_service.thread_repo.search_keyword_threads",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await search_service.search_threads(
            session,
            _settings(),
            openai_client=AsyncMock(),
            query=(
                "can you give me the latest on Omason timber products? what s happening with them?"
            ),
        )
    assert captured["text"] == "Omason timber products"


@pytest.mark.db
@pytest.mark.asyncio
async def test_unapostrophized_query_finds_apostrophe_name_in_index(db_session) -> None:
    """Postgres splits O'Mason into o + mason, so Omason:* misses unless FTS folds '."""
    from app.models.db.email_embedding import EmailEmbedding
    from app.models.db.message import Message
    from app.models.db.thread import Thread

    now = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    thread_id = uuid.uuid4()
    thread = Thread(
        id=thread_id,
        mailbox=SALES,
        conversation_id="conv-apostrophe-vendor",
        subject="Background check inquiry",
        state="DRAFTED",
        urgency="NORMAL",
        last_message_at=now,
    )
    db_session.add(thread)
    await db_session.flush()
    message = Message(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="vendor@example.com",
        body_text="Please set up O'Mason Timber Products for screening.",
        body_preview="Please set up O'Mason Timber Products for screening.",
        received_at=now,
        to_recipients=[SALES],
        cc_recipients=[],
    )
    db_session.add(message)
    await db_session.flush()
    db_session.add(
        EmailEmbedding(
            mailbox=SALES,
            conversation_id="conv-apostrophe-vendor",
            sender_email="vendor@example.com",
            recipient_emails=[SALES],
            cc_emails=[],
            embedding=[0.0] * _embedding_dim(),
            sent_at=now,
            body_preview="Please set up O'Mason Timber Products for screening.",
            search_document=(
                "From: vendor@example.com | Subject: Background check inquiry\n\n"
                "Please set up O'Mason Timber Products for screening."
            ),
            message_id=message.id,
        )
    )
    await db_session.commit()

    result = await search_service.search_threads(
        db_session,
        _settings(),
        openai_client=None,
        query="Omason",
        mode="keyword",
    )
    assert [hit.thread_id for hit in result.hits] == [thread_id]


@pytest.mark.db
@pytest.mark.asyncio
async def test_unembedded_thread_is_found_by_subject_and_sender(db_session) -> None:
    """Mail never embedded is still in threads/messages and must be searchable."""
    from app.models.db.message import Message
    from app.models.db.thread import Thread

    now = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    thread_id = uuid.uuid4()
    db_session.add(
        Thread(
            id=thread_id,
            mailbox=SALES,
            conversation_id="conv-unembedded-lumber",
            subject="ACH Form - O'Mason Lumber Products",
            state="DRAFTED",
            urgency="NORMAL",
            last_message_at=now,
        )
    )
    await db_session.flush()
    db_session.add(
        Message(
            id=uuid.uuid4(),
            thread_id=thread_id,
            graph_message_id=str(uuid.uuid4()),
            direction="inbound",
            sender="ashley@sample-materials.example.com",
            body_text="Attached is the ACH form.",
            body_preview="Attached is the ACH form.",
            received_at=now,
            to_recipients=[SALES],
            cc_recipients=[],
        )
    )
    await db_session.commit()

    result = await search_service.search_threads(
        db_session,
        _settings(),
        openai_client=None,
        query=(
            "can you give me the latest on Omason timber products? what s happening with them?"
        ),
    )
    hit_ids = [hit.thread_id for hit in result.hits]
    assert thread_id in hit_ids
    found = next(hit for hit in result.hits if hit.thread_id == thread_id)
    assert "O'Mason Lumber Products" in (found.subject or "")
