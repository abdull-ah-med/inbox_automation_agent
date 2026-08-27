"""Unit tests for Flow B cross-thread context resolution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import GraphClientError
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import EmailTriageState
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.models.schemas.graph import GraphMessageBodySchema, GraphMessageSchema
from app.services import context_service


def _email(**overrides: object) -> EmailMessageSchema:
    base: dict[str, object] = {
        "message_id": "graph-m1",
        "conversation_id": "conv-current",
        "mailbox": "elise@example.com",
        "sender": "vendor@example.com",
        "subject": "Follow up on intake",
        "body_text": "As discussed last week, please send the packet.",
        "body_preview": "As discussed last week",
        "received_at": datetime(2026, 7, 23, tzinfo=UTC),
        "direction": EmailDirectionEnum.INBOUND,
        "to_recipients": ["elise@example.com"],
        "cc_recipients": [],
    }
    base.update(overrides)
    return EmailMessageSchema(**base)  # type: ignore[arg-type]


def _state() -> EmailTriageState:
    email = _email()
    return EmailTriageState(
        original_email=email,
        thread_context=ThreadContextSchema(
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            subject=email.subject,
            messages=[email],
        ),
        triage=TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary="Send packet",
            needs_context=True,
            context_reason="refers to prior discussion",
        ),
        draft_status="PENDING",
    )


def _settings() -> Settings:
    return Settings(
        environment="local",
        embedding_model="text-embedding-3-small",
        embedding_dimension=8,
        embedding_min_similarity=0.78,
        embedding_candidate_k=15,
        openai_api_key="test-key",
    )


def _graph_msg(msg_id: str, *, subject: str = "Prior thread") -> GraphMessageSchema:
    return GraphMessageSchema(
        id=msg_id,
        subject=subject,
        bodyPreview="Earlier discussion",
        body=GraphMessageBodySchema(contentType="text", content="Earlier discussion body"),
        receivedDateTime=datetime(2026, 7, 1, tzinfo=UTC),
        conversationId="conv-matched",
    )


def _session_factory() -> MagicMock:
    """``async with factory() as session, session.begin():`` compatible mock."""
    session = AsyncMock()
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)

    factory_cm = AsyncMock()
    factory_cm.__aenter__ = AsyncMock(return_value=session)
    factory_cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=factory_cm)


@pytest.mark.asyncio
async def test_resolve_above_threshold_fetches_thread_and_persists_link() -> None:
    state = _state()
    settings = _settings()
    source_pk = uuid.uuid4()
    matched_embedding_id = uuid.uuid4()
    vector = [0.1] * settings.embedding_dimension
    stored = EmbeddingMatchSchema(
        id=uuid.uuid4(),
        conversation_id=state.original_email.conversation_id,
        similarity_score=1.0,
        message_id=source_pk,
    )
    match = EmbeddingMatchSchema(
        id=matched_embedding_id,
        conversation_id="conv-matched",
        similarity_score=0.91,
        message_id=uuid.uuid4(),
    )
    graph_client = MagicMock()
    graph_client.list_thread_messages = AsyncMock(
        return_value=[_graph_msg("prior-1"), _graph_msg("prior-2")]
    )
    graph_client.send_mail = AsyncMock()
    graph_client.create_reply = AsyncMock()

    with (
        patch(
            "app.services.context_service.embedding_service.embed_email",
            new=AsyncMock(return_value=vector),
        ),
        patch(
            "app.services.context_service.embedding_service.store_email_embedding",
            new=AsyncMock(return_value=stored),
        ),
        patch(
            "app.services.context_service.embedding_repo.search_similar",
            new=AsyncMock(return_value=[match]),
        ) as search,
        patch(
            "app.services.context_service.embedding_repo.search_fts",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context_service._messages_from_db",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.context_service.thread_link_repo.insert_thread_link",
            new=AsyncMock(return_value=uuid.uuid4()),
        ) as link,
    ):
        result = await context_service.resolve_cross_thread_context(
            state,
            session_factory=_session_factory(),
            graph_client=graph_client,
            openai_client=AsyncMock(),
            settings=settings,
        )

    assert result is not None
    assert result.matched_conversation_id == "conv-matched"
    # Aggregated RRF conversation score for a single vector-leg rank-1 hit.
    expected_score = 1.0 / (60 + 1)
    assert abs(result.similarity_score - expected_score) < 1e-6
    assert len(result.thread_messages) == 2
    search.assert_awaited_once()
    assert search.await_args.kwargs["exclude_conversation_id"] == "conv-current"
    assert search.await_args.kwargs["min_similarity"] == 0.78
    graph_client.list_thread_messages.assert_awaited_once_with(
        "elise@example.com",
        "conv-matched",
    )
    link.assert_awaited_once()
    assert link.await_args.kwargs["source_message_id"] == source_pk
    assert link.await_args.kwargs["matched_embedding_id"] == matched_embedding_id
    assert link.await_args.kwargs["matched_conversation_id"] == "conv-matched"
    assert abs(link.await_args.kwargs["similarity_score"] - expected_score) < 1e-6
    # Read-only: only list_thread_messages used.
    graph_client.send_mail.assert_not_called()
    graph_client.create_reply.assert_not_called()
    assert graph_client.list_thread_messages.await_count == 1


@pytest.mark.asyncio
async def test_resolve_below_threshold_returns_none() -> None:
    state = _state()
    settings = _settings()
    vector = [0.1] * settings.embedding_dimension
    stored = EmbeddingMatchSchema(
        id=uuid.uuid4(),
        conversation_id=state.original_email.conversation_id,
        similarity_score=1.0,
        message_id=uuid.uuid4(),
    )
    graph_client = MagicMock()
    graph_client.list_thread_messages = AsyncMock()

    with (
        patch(
            "app.services.context_service.embedding_service.embed_email",
            new=AsyncMock(return_value=vector),
        ),
        patch(
            "app.services.context_service.embedding_service.store_email_embedding",
            new=AsyncMock(return_value=stored),
        ),
        patch(
            "app.services.context_service.embedding_repo.search_similar",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context_service.embedding_repo.search_fts",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context_service.thread_link_repo.insert_thread_link",
            new=AsyncMock(),
        ) as link,
    ):
        result = await context_service.resolve_cross_thread_context(
            state,
            session_factory=_session_factory(),
            graph_client=graph_client,
            openai_client=AsyncMock(),
            settings=settings,
        )

    assert result is None
    graph_client.list_thread_messages.assert_not_awaited()
    link.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_graph_error_returns_none_safely() -> None:
    state = _state()
    settings = _settings()
    vector = [0.1] * settings.embedding_dimension
    stored = EmbeddingMatchSchema(
        id=uuid.uuid4(),
        conversation_id=state.original_email.conversation_id,
        similarity_score=1.0,
        message_id=uuid.uuid4(),
    )
    match = EmbeddingMatchSchema(
        id=uuid.uuid4(),
        conversation_id="conv-matched",
        similarity_score=0.88,
        message_id=uuid.uuid4(),
    )
    graph_client = MagicMock()
    graph_client.list_thread_messages = AsyncMock(side_effect=GraphClientError("Graph unavailable"))

    with (
        patch(
            "app.services.context_service.embedding_service.embed_email",
            new=AsyncMock(return_value=vector),
        ),
        patch(
            "app.services.context_service.embedding_service.store_email_embedding",
            new=AsyncMock(return_value=stored),
        ),
        patch(
            "app.services.context_service.embedding_repo.search_similar",
            new=AsyncMock(return_value=[match]),
        ),
        patch(
            "app.services.context_service.embedding_repo.search_fts",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context_service._messages_from_db",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.context_service.thread_link_repo.insert_thread_link",
            new=AsyncMock(),
        ) as link,
    ):
        result = await context_service.resolve_cross_thread_context(
            state,
            session_factory=_session_factory(),
            graph_client=graph_client,
            openai_client=AsyncMock(),
            settings=settings,
        )

    assert result is None
    link.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_prefers_corroborated_conversation() -> None:
    """Fix 6 regression: three corroborating hits beat one stronger single hit."""
    state = _state()
    settings = _settings()
    vector = [0.1] * settings.embedding_dimension
    stored = EmbeddingMatchSchema(
        id=uuid.uuid4(),
        conversation_id=state.original_email.conversation_id,
        similarity_score=1.0,
        message_id=uuid.uuid4(),
    )

    def _match(conv: str, score: float) -> EmbeddingMatchSchema:
        return EmbeddingMatchSchema(
            id=uuid.uuid4(),
            conversation_id=conv,
            similarity_score=score,
            message_id=uuid.uuid4(),
        )

    # Vector ranks (1-indexed): unique fillers + A@2 + B@4,6,9 (Fix 6 layout).
    vector_matches = [
        _match("conv-filler-1", 0.95),
        _match("conv-a", 0.92),
        _match("conv-filler-3", 0.90),
        _match("conv-b", 0.88),
        _match("conv-filler-5", 0.86),
        _match("conv-b", 0.84),
        _match("conv-filler-7", 0.82),
        _match("conv-filler-8", 0.80),
        _match("conv-b", 0.78),
    ]
    graph_client = MagicMock()
    graph_client.list_thread_messages = AsyncMock(return_value=[_graph_msg("prior-b")])
    graph_client.send_mail = AsyncMock()
    graph_client.create_reply = AsyncMock()

    with (
        patch(
            "app.services.context_service.embedding_service.embed_email",
            new=AsyncMock(return_value=vector),
        ),
        patch(
            "app.services.context_service.embedding_service.store_email_embedding",
            new=AsyncMock(return_value=stored),
        ),
        patch(
            "app.services.context_service.embedding_repo.search_similar",
            new=AsyncMock(return_value=vector_matches),
        ),
        patch(
            "app.services.context_service.embedding_repo.search_fts",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context_service._messages_from_db",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.context_service.thread_link_repo.insert_thread_link",
            new=AsyncMock(return_value=uuid.uuid4()),
        ) as link,
    ):
        result = await context_service.resolve_cross_thread_context(
            state,
            session_factory=_session_factory(),
            graph_client=graph_client,
            openai_client=AsyncMock(),
            settings=settings,
        )

    assert result is not None
    assert result.matched_conversation_id == "conv-b"
    graph_client.list_thread_messages.assert_awaited_once_with(
        "elise@example.com",
        "conv-b",
    )
    assert link.await_args.kwargs["matched_conversation_id"] == "conv-b"
    graph_client.send_mail.assert_not_called()
    graph_client.create_reply.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_prefers_local_db_messages_over_graph() -> None:
    state = _state()
    settings = _settings()
    vector = [0.1] * settings.embedding_dimension
    stored = EmbeddingMatchSchema(
        id=uuid.uuid4(),
        conversation_id=state.original_email.conversation_id,
        similarity_score=1.0,
        message_id=uuid.uuid4(),
    )
    match = EmbeddingMatchSchema(
        id=uuid.uuid4(),
        conversation_id="conv-matched",
        similarity_score=0.91,
        message_id=uuid.uuid4(),
    )
    local_msgs = [
        _email(
            message_id="local-1",
            conversation_id="conv-matched",
            body_clean="Local cleaned body",
            summary_one_line="Local summary",
        )
    ]
    graph_client = MagicMock()
    graph_client.list_thread_messages = AsyncMock()
    graph_client.send_mail = AsyncMock()
    graph_client.create_reply = AsyncMock()

    with (
        patch(
            "app.services.context_service.embedding_service.embed_email",
            new=AsyncMock(return_value=vector),
        ),
        patch(
            "app.services.context_service.embedding_service.store_email_embedding",
            new=AsyncMock(return_value=stored),
        ),
        patch(
            "app.services.context_service.embedding_repo.search_similar",
            new=AsyncMock(return_value=[match]),
        ),
        patch(
            "app.services.context_service.embedding_repo.search_fts",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context_service._messages_from_db",
            new=AsyncMock(return_value=local_msgs),
        ),
        patch(
            "app.services.context_service.thread_link_repo.insert_thread_link",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
    ):
        result = await context_service.resolve_cross_thread_context(
            state,
            session_factory=_session_factory(),
            graph_client=graph_client,
            openai_client=AsyncMock(),
            settings=settings,
        )

    assert result is not None
    assert result.thread_messages == local_msgs
    graph_client.list_thread_messages.assert_not_awaited()
    graph_client.send_mail.assert_not_called()
    graph_client.create_reply.assert_not_called()


@pytest.mark.asyncio
async def test_messages_from_db_builds_schemas() -> None:
    from app.repositories.message_repo import MessageSchema
    from app.repositories.thread_repo import ThreadSchema

    thread_id = uuid.uuid4()
    thread = ThreadSchema(
        id=thread_id,
        mailbox="elise@example.com",
        conversation_id="conv-matched",
        subject="Prior",
        state="NEW",
        last_message_at=datetime(2026, 7, 1, tzinfo=UTC),
        last_updated_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    row = MessageSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id="local-1",
        direction="inbound",
        sender="vendor@example.com",
        body_text="raw",
        body_preview="raw",
        body_content_type="text",
        body_clean="cleaned local",
        body_clean_version=1,
        received_at=datetime(2026, 7, 1, tzinfo=UTC),
        to_recipients=[],
        cc_recipients=[],
        summary_one_line="one line",
        summary_json={"one_line": "one line"},
    )

    with (
        patch(
            "app.services.context_service.thread_repo.get_by_conversation_id",
            new=AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.context_service.message_repo.list_by_thread",
            new=AsyncMock(return_value=[row]),
        ),
    ):
        msgs = await context_service._messages_from_db(
            _session_factory(),
            mailbox="elise@example.com",
            conversation_id="conv-matched",
        )

    assert msgs is not None
    assert len(msgs) == 1
    assert msgs[0].body_clean == "cleaned local"
    assert msgs[0].summary_one_line == "one line"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_embed_fails() -> None:
    with patch(
        "app.services.context_service.embedding_service.embed_email",
        new=AsyncMock(side_effect=RuntimeError("openai down")),
    ):
        result = await context_service.resolve_cross_thread_context(
            _state(),
            session_factory=_session_factory(),
            graph_client=MagicMock(),
            openai_client=AsyncMock(),
            settings=_settings(),
        )
    assert result is None


@pytest.mark.asyncio
async def test_messages_from_db_returns_none_when_thread_missing() -> None:
    with patch(
        "app.services.context_service.thread_repo.get_by_conversation_id",
        new=AsyncMock(return_value=None),
    ):
        msgs = await context_service._messages_from_db(
            _session_factory(),
            mailbox="elise@example.com",
            conversation_id="missing",
        )
    assert msgs is None


@pytest.mark.asyncio
async def test_messages_from_db_recomputes_missing_clean() -> None:
    from app.repositories.message_repo import MessageSchema
    from app.repositories.thread_repo import ThreadSchema

    thread_id = uuid.uuid4()
    thread = ThreadSchema(
        id=thread_id,
        mailbox="elise@example.com",
        conversation_id="conv-matched",
        subject="Prior",
        state="NEW",
        last_message_at=datetime(2026, 7, 1, tzinfo=UTC),
        last_updated_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    row = MessageSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id="local-1",
        direction="inbound",
        sender="vendor@example.com",
        body_text="Please reply with the packet today.",
        body_preview="Please reply",
        body_content_type="text",
        body_clean=None,
        received_at=datetime(2026, 7, 1, tzinfo=UTC),
        to_recipients=[],
        cc_recipients=[],
    )

    with (
        patch(
            "app.services.context_service.thread_repo.get_by_conversation_id",
            new=AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.context_service.message_repo.list_by_thread",
            new=AsyncMock(return_value=[row]),
        ),
    ):
        msgs = await context_service._messages_from_db(
            _session_factory(),
            mailbox="elise@example.com",
            conversation_id="conv-matched",
        )

    assert msgs is not None
    assert msgs[0].body_clean
    assert "packet" in (msgs[0].body_clean or "")
