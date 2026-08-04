"""Flow B — hybrid cross-thread context (vector + FTS RRF → best conversation).

Read-only Graph only (``list_thread_messages``). Failures return ``None`` so the
pipeline can fall back to Flow A without raising into ingest.

Search is mailbox-scoped so related-thread context cannot leak across
``TARGET_MAILBOXES``.

Transaction discipline: OpenAI embed and Graph HTTP run *outside* any DB
transaction. Short ``begin()`` blocks cover store/search and thread_link writes.

``embedding_final_conversations`` defaults to 1 (v1 prompt still shows one
related thread). Soft-margin selection already supports N>1; widening
``CrossThreadContextSchema`` to a list is deferred.
"""

from __future__ import annotations

import structlog
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import GraphClientError
from app.graph.client import GraphClient
from app.llm.context_pack import anchor_first_and_newest
from app.llm.email_clean import CLEAN_VERSION, clean_email_body
from app.models.schemas.email import EmailMessageSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema, EmailTriageState
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.repositories import embedding_repo, message_repo, thread_link_repo, thread_repo
from app.services import embedding_service
from app.services.ingestion_service import _to_email_message_schema
from app.services.rrf import (
    aggregate_conversation_scores,
    best_hit_for_conversation,
    rrf_fuse,
    select_related_conversations,
)

logger = structlog.get_logger(__name__)

_MAX_CROSS_THREAD_MESSAGES = 30


async def resolve_cross_thread_context(
    state: EmailTriageState,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    graph_client: GraphClient,
    openai_client: AsyncOpenAI,
    settings: Settings,
) -> CrossThreadContextSchema | None:
    """Embed, hybrid-search, and fetch a related thread when above threshold."""
    email = state.original_email
    mailbox = email.mailbox
    conversation_id = email.conversation_id

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

    query_doc = embedding_service.build_search_document(email)
    candidate_k = settings.embedding_candidate_k

    try:
        async with session_factory() as session, session.begin():
            stored = await embedding_service.store_email_embedding(
                session,
                email=email,
                embedding=vector,
                search_document=query_doc,
                embed_clean_version=CLEAN_VERSION,
            )
            vector_matches = await embedding_repo.search_similar(
                session,
                embedding=vector,
                min_similarity=settings.embedding_min_similarity,
                top_k=candidate_k,
                mailbox=mailbox,
                exclude_conversation_id=conversation_id,
            )
            fts_matches = await embedding_repo.search_fts(
                session,
                query_text=query_doc,
                top_k=candidate_k,
                mailbox=mailbox,
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

    if not vector_matches and not fts_matches:
        logger.info(
            "cross_thread_no_match",
            conversation_id=conversation_id,
            mailbox=mailbox,
            message_id=email.message_id,
            min_similarity=settings.embedding_min_similarity,
        )
        return None

    fused = rrf_fuse(
        [(m.id, m.conversation_id, m.message_id) for m in vector_matches],
        [(m.id, m.conversation_id, m.message_id) for m in fts_matches],
        k=settings.rrf_k,
    )
    conv_scores = aggregate_conversation_scores(
        fused,
        bonus=settings.embedding_corroboration_bonus,
        max_bonus_hits=settings.embedding_corroboration_max_hits,
    )
    ranked = sorted(conv_scores.items(), key=lambda kv: kv[1], reverse=True)
    selected = select_related_conversations(
        ranked,
        max_conversations=settings.embedding_final_conversations,
        margin=settings.embedding_secondary_margin,
    )
    if not selected:
        return None

    # v1: only the top conversation is surfaced to the prompt.
    matched_conversation_id = selected[0]
    conversation_score = conv_scores[matched_conversation_id]
    top_hit = best_hit_for_conversation(fused, matched_conversation_id)
    if top_hit is None:
        return None

    thread_messages = await _load_related_thread(
        session_factory=session_factory,
        graph_client=graph_client,
        mailbox=mailbox,
        matched_conversation_id=matched_conversation_id,
        current_conversation_id=conversation_id,
        message_id=email.message_id,
        conversation_score=conversation_score,
    )
    if thread_messages is None:
        return None

    top_match = EmbeddingMatchSchema(
        id=top_hit.embedding_id,
        conversation_id=matched_conversation_id,
        similarity_score=min(1.0, conversation_score),
        message_id=top_hit.message_id,
    )
    await _persist_thread_link(
        session_factory,
        email_message_id=email.message_id,
        stored=stored,
        top=top_match,
        matched_conversation_id=matched_conversation_id,
        similarity_score=min(1.0, conversation_score),
        conversation_id=conversation_id,
        mailbox=mailbox,
    )

    logger.info(
        "cross_thread_match",
        conversation_id=conversation_id,
        mailbox=mailbox,
        message_id=email.message_id,
        matched_conversation_id=matched_conversation_id,
        similarity_score=conversation_score,
        thread_message_count=len(thread_messages),
    )
    return CrossThreadContextSchema(
        matched_conversation_id=matched_conversation_id,
        similarity_score=min(1.0, conversation_score),
        thread_messages=thread_messages,
    )


async def _load_related_thread(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    graph_client: GraphClient,
    mailbox: str,
    matched_conversation_id: str,
    current_conversation_id: str,
    message_id: str,
    conversation_score: float,
) -> list[EmailMessageSchema] | None:
    """Prefer local DB messages; Graph fetch only if local thread incomplete."""
    local = await _messages_from_db(
        session_factory,
        mailbox=mailbox,
        conversation_id=matched_conversation_id,
    )
    if local is not None and len(local) > 0:
        return anchor_first_and_newest(local, cap=_MAX_CROSS_THREAD_MESSAGES)

    try:
        graph_messages = await graph_client.list_thread_messages(
            mailbox,
            matched_conversation_id,
        )
    except GraphClientError:
        logger.warning(
            "cross_thread_graph_failed",
            conversation_id=current_conversation_id,
            mailbox=mailbox,
            message_id=message_id,
            matched_conversation_id=matched_conversation_id,
            similarity_score=conversation_score,
            exc_info=True,
        )
        return None
    except Exception:
        logger.exception(
            "cross_thread_graph_failed",
            conversation_id=current_conversation_id,
            mailbox=mailbox,
            message_id=message_id,
            matched_conversation_id=matched_conversation_id,
            similarity_score=conversation_score,
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
    return anchor_first_and_newest(thread_messages, cap=_MAX_CROSS_THREAD_MESSAGES)


async def _messages_from_db(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    mailbox: str,
    conversation_id: str,
) -> list[EmailMessageSchema] | None:
    try:
        async with session_factory() as session:
            thread = await thread_repo.get_by_conversation_id(
                session,
                conversation_id,
                mailbox=mailbox,
            )
            if thread is None:
                return None
            rows = await message_repo.list_by_thread(session, thread.id)
            if not rows:
                return None
            from app.models.schemas.email import EmailDirectionEnum

            out: list[EmailMessageSchema] = []
            for row in rows:
                body_clean = row.body_clean
                if not body_clean:
                    body_clean = clean_email_body(
                        row.body_text,
                        content_type=row.body_content_type or "text",
                    ).body_clean
                out.append(
                    EmailMessageSchema(
                        message_id=row.graph_message_id,
                        conversation_id=conversation_id,
                        mailbox=mailbox,
                        sender=row.sender,
                        subject=thread.subject,
                        body_text=row.body_text,
                        body_preview=row.body_preview,
                        body_content_type=row.body_content_type or "text",
                        body_clean=body_clean,
                        received_at=row.received_at,
                        direction=(
                            EmailDirectionEnum(row.direction)
                            if row.direction in {e.value for e in EmailDirectionEnum}
                            else EmailDirectionEnum.INBOUND
                        ),
                        to_recipients=list(row.to_recipients or []),
                        cc_recipients=list(row.cc_recipients or []),
                        has_attachments=bool(row.has_attachments),
                        summary_one_line=row.summary_one_line,
                        summary_json=row.summary_json,
                    )
                )
            return out
    except Exception:
        logger.exception(
            "cross_thread_local_load_failed",
            mailbox=mailbox,
            matched_conversation_id=conversation_id,
        )
        return None


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
    """Short txn for ``thread_links`` only.

    ``similarity_score`` stores the aggregated conversation_score (RRF +
    corroboration), not a raw single-message cosine.
    """
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
