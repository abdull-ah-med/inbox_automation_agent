"""Flow B — resolve related prior threads via embedding similarity + Graph fetch.

Read-only Graph only (``list_thread_messages``). Failures return ``None`` so the
pipeline can fall back to Flow A without raising into ingest.

Transaction discipline (Postgres ``idle_in_transaction_session_timeout`` /
SQLAlchemy async session guidance): OpenAI embed and Graph HTTP run *outside*
any DB transaction. Short ``begin()`` blocks cover store/search and thread_link
writes only.

Refs:
- https://docs.sqlalchemy.org/en/20/orm/session_transaction.html
- https://www.postgresql.org/docs/current/runtime-config-client.html
- https://learn.microsoft.com/en-us/graph/api/user-list-messages
"""

from __future__ import annotations

import structlog
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import GraphClientError
from app.graph.client import GraphClient
from app.models.schemas.email_triage_state import CrossThreadContextSchema, EmailTriageState
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.repositories import embedding_repo, message_repo, thread_link_repo
from app.services import embedding_service
from app.services.ingestion_service import _to_email_message_schema

logger = structlog.get_logger(__name__)

# Cap injected thread size so Sonnet stays within a reasonable prompt budget.
# Messages are oldest-first from Graph; drop oldest when over the limit.
_MAX_CROSS_THREAD_MESSAGES = 30


async def resolve_cross_thread_context(
    state: EmailTriageState,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    graph_client: GraphClient,
    openai_client: AsyncOpenAI,
    settings: Settings,
) -> CrossThreadContextSchema | None:
    """Embed, similarity-search, and fetch a related thread when above threshold.

    Returns ``None`` on below-threshold / empty results / Graph or embed failures
    (safe Flow A fallback). Never raises into the caller for those cases.

    Network I/O (OpenAI + Graph) never holds an open DB transaction.
    """
    email = state.original_email
    mailbox = email.mailbox
    conversation_id = email.conversation_id

    # --- OpenAI embed (no DB connection held) ---
    try:
        vector = await embedding_service.embed_email(
            email,
            client=openai_client,
            settings=settings,
        )
    except Exception:
        logger.exception(
            "cross_thread_embed_failed",
            conversation_id=conversation_id,
            mailbox=mailbox,
            message_id=email.message_id,
        )
        return None

    # --- Short txn: persist embedding + similarity search ---
    try:
        async with session_factory() as session, session.begin():
            stored = await embedding_service.store_email_embedding(
                session,
                email=email,
                embedding=vector,
            )
            matches = await embedding_repo.search_similar(
                session,
                embedding=vector,
                min_similarity=settings.embedding_min_similarity,
                top_k=settings.embedding_top_k,
                exclude_conversation_id=conversation_id,
            )
    except Exception:
        logger.exception(
            "cross_thread_embed_store_or_search_failed",
            conversation_id=conversation_id,
            mailbox=mailbox,
            message_id=email.message_id,
        )
        return None

    if not matches:
        logger.info(
            "cross_thread_no_match",
            conversation_id=conversation_id,
            mailbox=mailbox,
            message_id=email.message_id,
            min_similarity=settings.embedding_min_similarity,
        )
        return None

    top = matches[0]
    matched_conversation_id = top.conversation_id
    similarity_score = top.similarity_score

    # --- Graph read (no DB connection held) ---
    try:
        graph_messages = await graph_client.list_thread_messages(
            mailbox,
            matched_conversation_id,
        )
    except GraphClientError:
        logger.warning(
            "cross_thread_graph_failed",
            conversation_id=conversation_id,
            mailbox=mailbox,
            message_id=email.message_id,
            matched_conversation_id=matched_conversation_id,
            similarity_score=similarity_score,
            exc_info=True,
        )
        return None
    except Exception:
        logger.exception(
            "cross_thread_graph_failed",
            conversation_id=conversation_id,
            mailbox=mailbox,
            message_id=email.message_id,
            matched_conversation_id=matched_conversation_id,
            similarity_score=similarity_score,
        )
        return None

    thread_messages = [
        _to_email_message_schema(
            mailbox=mailbox,
            conversation_id=matched_conversation_id,
            message=msg,
        )
        for msg in graph_messages
    ]
    if len(thread_messages) > _MAX_CROSS_THREAD_MESSAGES:
        # Keep the newest messages (list is oldest-first).
        thread_messages = thread_messages[-_MAX_CROSS_THREAD_MESSAGES:]

    await _persist_thread_link(
        session_factory,
        email_message_id=email.message_id,
        stored=stored,
        top=top,
        matched_conversation_id=matched_conversation_id,
        similarity_score=similarity_score,
        conversation_id=conversation_id,
        mailbox=mailbox,
    )

    logger.info(
        "cross_thread_match",
        conversation_id=conversation_id,
        mailbox=mailbox,
        message_id=email.message_id,
        matched_conversation_id=matched_conversation_id,
        similarity_score=similarity_score,
        thread_message_count=len(thread_messages),
    )
    return CrossThreadContextSchema(
        matched_conversation_id=matched_conversation_id,
        similarity_score=similarity_score,
        thread_messages=thread_messages,
    )


async def _persist_thread_link(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email_message_id: str,
    stored: EmbeddingMatchSchema,
    top: EmbeddingMatchSchema,
    matched_conversation_id: str,
    similarity_score: float,
    conversation_id: str,
    mailbox: str,
) -> None:
    """Short txn for ``thread_links`` only."""
    try:
        async with session_factory() as session, session.begin():
            source_pk = stored.message_id
            if source_pk is None:
                existing = await message_repo.get_by_graph_id(session, email_message_id)
                if existing is not None:
                    source_pk = existing.id

            if source_pk is None:
                logger.warning(
                    "cross_thread_link_skipped_missing_source",
                    conversation_id=conversation_id,
                    mailbox=mailbox,
                    message_id=email_message_id,
                    matched_conversation_id=matched_conversation_id,
                )
                return

            await thread_link_repo.insert_thread_link(
                session,
                source_message_id=source_pk,
                matched_embedding_id=top.id,
                matched_conversation_id=matched_conversation_id,
                similarity_score=similarity_score,
            )
    except Exception:
        logger.exception(
            "cross_thread_link_persist_failed",
            conversation_id=conversation_id,
            mailbox=mailbox,
            message_id=email_message_id,
            matched_conversation_id=matched_conversation_id,
        )
