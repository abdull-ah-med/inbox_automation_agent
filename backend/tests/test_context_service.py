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
        embedding_top_k=3,
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
    factory = MagicMock(return_value=factory_cm)
    return factory


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
    assert result.similarity_score == 0.91
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
    assert link.await_args.kwargs["similarity_score"] == 0.91
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
