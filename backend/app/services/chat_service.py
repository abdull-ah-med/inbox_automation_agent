"""Read-only NL chat: tool-mediated RAG, then a grounded answer with citations.

Never sends, approves, rejects, or writes mail. Citations are always built from
tool hits, never from model-invented thread ids.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ChatError
from app.core.sanitize import sanitize_user_text
from app.llm.chat import (
    ChatDeltaScrubber,
    iter_chat_agent,
    run_chat_agent,
    sanitize_chat_answer,
)
from app.llm.chat_prompts import (
    NO_MATCH_ANSWER,
    OUT_OF_SCOPE_ANSWER,
    WRITE_REFUSAL_ANSWER,
    no_match_answer,
)
from app.llm.groundedness import verify_grounded
from app.models.schemas.chat import (
    CHAT_DEFAULT_LIMIT,
    ChatAskResponse,
    ChatCitation,
    ChatHistoryTurn,
)
from app.models.schemas.search import SearchHit
from app.repositories import chat_cache_repo
from app.services import chat_session_service, embedding_service, search_service
from app.services.chat_intent import ChatIntent, classify_chat_intent
from app.services.chat_query import retrieval_message
from app.services.chat_tools import execute_chat_tool

logger = structlog.get_logger(__name__)

_WRITE_PHRASES: tuple[str, ...] = (
    "send this",
    "send it",
    "send the draft",
    "send the reply",
    "send the email",
    "approve this",
    "approve it",
    "approve the draft",
    "reject this",
    "reject it",
    "delete this",
    "delete it",
    "delete the thread",
    "move to junk",
    "move this to junk",
    "mark as spam",
    "mark this as spam",
)

STATUS_SEARCHING = "Searching your inbox…"
STATUS_DRAFTING = "Drafting answer…"


def detect_write_intent(message: str) -> bool:
    """Light phrase match for send/approve/reject/delete/move-to-junk asks."""
    lowered = " ".join((message or "").lower().split())
    return any(phrase in lowered for phrase in _WRITE_PHRASES)


def _merge_history(
    client_history: list[ChatHistoryTurn] | None,
    session_history: list[ChatHistoryTurn] | None,
) -> list[ChatHistoryTurn]:
    """Prefer explicit client history; otherwise use the durable session transcript."""
    if client_history:
        return list(client_history)
    return list(session_history or [])


async def _load_session_history(
    session: AsyncSession,
    *,
    session_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> list[ChatHistoryTurn]:
    if session_id is None or user_id is None:
        return []
    row = await chat_session_service.get_session(
        session,
        session_id=session_id,
        user_id=user_id,
    )
    if row is None:
        return []
    return chat_session_service.history_from_session(row)


async def _persist_session_turn(
    session: AsyncSession,
    *,
    session_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    user_message: str,
    assistant_message: str,
    citations: list[ChatCitation],
) -> None:
    if session_id is None or user_id is None or not assistant_message.strip():
        return
    try:
        await chat_session_service.append_turn(
            session,
            session_id=session_id,
            user_id=user_id,
            user_message=user_message,
            assistant_message=assistant_message,
            citations=[{"thread_id": str(c.thread_id), "subject": c.subject} for c in citations],
        )
    except Exception:
        logger.warning("chat_session_persist_failed", session_id=str(session_id))


def intent_uses_semantic_cache(intent: ChatIntent) -> bool:
    """Follow-ups and recent-list asks are deterministic; skip the embed round-trip."""
    return intent not in {ChatIntent.FOLLOW_UP, ChatIntent.OVERVIEW}


def _reading_status(hit_count: int) -> str:
    if hit_count == 1:
        return "Reading 1 thread…"
    return f"Reading {hit_count} threads…"


def thread_url_path(thread_id: uuid.UUID) -> str:
    return f"/threads/{thread_id}"


def citations_from_hits(hits: list[SearchHit]) -> list[ChatCitation]:
    return [
        ChatCitation(
            thread_id=hit.thread_id,
            mailbox=hit.mailbox,
            subject=hit.subject,
            state=hit.state,
            urgency=hit.urgency,
            snippet=hit.snippet or None,
            url_path=thread_url_path(hit.thread_id),
        )
        for hit in hits
    ]


def _write_refusal_answer(hits: list[SearchHit]) -> str:
    if not hits:
        return f"{WRITE_REFUSAL_ANSWER}\n\n{NO_MATCH_ANSWER}"
    return WRITE_REFUSAL_ANSWER


def _cleaned_history(
    history: list[ChatHistoryTurn] | None,
) -> list[ChatHistoryTurn]:
    cleaned: list[ChatHistoryTurn] = []
    for turn in history or []:
        text = sanitize_user_text(turn.content)
        if not text:
            continue
        cleaned.append(
            ChatHistoryTurn(
                role=turn.role,
                content=text,
                citations=list(turn.citations),
            )
        )
    return cleaned


def _agent_question(message: str, thread_id: uuid.UUID | None) -> str:
    if thread_id is None:
        return message
    return f"{message}\nResolved thread_id: {thread_id}"


def _out_of_scope_response(*, mailbox: str | None) -> ChatAskResponse:
    return ChatAskResponse(
        answer=OUT_OF_SCOPE_ANSWER,
        citations=[],
        retrieval_count=0,
        mailbox=mailbox,
        refused_write=False,
        grounded_verifier="SKIPPED",
    )


async def _groundedness_verdict(
    *,
    settings: Settings,
    client: AsyncAnthropic,
    answer: str,
    hits: list[SearchHit],
    grounded: bool,
) -> str:
    if not settings.chat_groundedness_enabled or not grounded:
        return "SKIPPED"
    result = await verify_grounded(
        answer,
        [{"text": hit.snippet or ""} for hit in hits],
        client=client,
        settings=settings,
    )
    if result.verdict == "UNSUPPORTED":
        logger.warning(
            "chat.groundedness_unsupported",
            unsupported_spans=result.unsupported_spans,
        )
    return result.verdict


def _mailbox_cache_key(mailbox: str | None, settings: Settings) -> str:
    return chat_cache_repo.scope_key(mailbox, mailbox_list=settings.mailbox_list)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _log_chat_ask(
    *,
    hit_count: int,
    mailbox: str | None,
    refused_write: bool,
    query_length: int,
    intent: object = None,
    tool_used_first: str | None = None,
    tool_iterations: int | None = None,
    grounded_verifier: str | None = None,
    retrieval_count: int | None = None,
    cached: bool = False,
    cache_similarity: float | None = None,
    ttft_ms: int | None = None,
    total_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
) -> None:
    logger.info(
        "chat.ask",
        hit_count=hit_count,
        mailbox=mailbox,
        refused_write=refused_write,
        query_length=query_length,
        intent=intent,
        tool_used_first=tool_used_first,
        tool_iterations=tool_iterations,
        retrieval_count=retrieval_count,
        cached=cached,
        cache_similarity=cache_similarity,
        grounded_verifier=grounded_verifier,
        ttft_ms=ttft_ms,
        total_ms=total_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )


def _is_uncacheable_answer(answer: str) -> bool:
    text = (answer or "").strip()
    if not text:
        return True
    if text in {NO_MATCH_ANSWER, OUT_OF_SCOPE_ANSWER, WRITE_REFUSAL_ANSWER}:
        return True
    if text.startswith(WRITE_REFUSAL_ANSWER) or text.startswith(OUT_OF_SCOPE_ANSWER):
        return True
    return "no matching threads" in text.lower()


def _is_unverified_verdict(verdict: str | None) -> bool:
    """H2: fail-closed. A cache HIT skips ``verify_grounded`` entirely (that
    is the point of caching), so an UNSUPPORTED or UNKNOWN (verifier
    timeout/parse-failure) answer must never enter the cache — it would be
    served, unverified, to every asker for the rest of its TTL.
    """
    return verdict in {"UNSUPPORTED", "UNKNOWN"}


def _ttl_seconds(settings: Settings, intent: ChatIntent | None) -> int:
    if intent in {ChatIntent.OVERVIEW, ChatIntent.AGGREGATION}:
        return settings.chat_semantic_cache_ttl_overview_sec
    return settings.chat_semantic_cache_ttl_search_sec


def _response_from_cache(hit: chat_cache_repo.ChatCacheHit) -> ChatAskResponse:
    payload = dict(hit.response_json)
    payload["cached"] = True
    payload["cache_similarity"] = hit.similarity
    return ChatAskResponse.model_validate(payload)


async def _lookup_semantic_cache(
    session: AsyncSession,
    settings: Settings,
    *,
    openai_client: AsyncOpenAI | None,
    message: str,
    mailbox: str | None,
    user_key: str | None,
    bypass_cache: bool,
) -> tuple[ChatAskResponse | None, list[float] | None]:
    if (
        bypass_cache
        or not settings.chat_semantic_cache_enabled
        or openai_client is None
        or not settings.openai_api_key.strip()
        or not (user_key or "").strip()
    ):
        return None, None
    try:
        embedding = await embedding_service.embed_text(
            chat_cache_repo.normalize_chat_query(message),
            client=openai_client,
            settings=settings,
        )
    except Exception:
        logger.warning("chat_cache_lookup_failed", mailbox=mailbox)
        return None, None
    try:
        hit = await chat_cache_repo.find_semantic_hit(
            session,
            mailbox_key=_mailbox_cache_key(mailbox, settings),
            user_key=user_key or "",
            query_embedding=embedding,
            similarity_threshold=settings.chat_semantic_cache_threshold,
            settings=settings,
        )
    except Exception:
        logger.warning("chat_cache_lookup_failed", mailbox=mailbox)
        await _rollback_session(session)
        return None, embedding
    if hit is None:
        return None, embedding
    cached_response = _response_from_cache(hit)
    if _is_unverified_verdict(cached_response.grounded_verifier):
        # Defense in depth: a row with this verdict should never have been
        # written (see _store_semantic_cache), but if one exists anyway
        # (legacy row, race), serving it as a HIT would bypass
        # verify_grounded for the rest of its TTL. Treat as a miss instead.
        logger.warning("chat_cache_hit_unverified_verdict", mailbox=mailbox)
        return None, embedding
    try:
        await chat_cache_repo.record_semantic_hit(session, hit.id)
        await _commit_session(session)
    except Exception:
        logger.warning("chat_cache_hit_count_failed", mailbox=mailbox)
        await _rollback_session(session)
    return cached_response, embedding


async def _rollback_session(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except Exception:
        return


async def _commit_session(session: AsyncSession) -> None:
    """`get_db_session` never auto-commits; callers must commit explicitly.

    Every write path in this module (session-turn persistence, semantic
    cache store) shares the request-scoped session. A commit failure here
    must not surface as a user-facing chat error — the answer was already
    computed — so we log and roll back instead of raising.
    """
    try:
        await session.commit()
    except Exception:
        logger.warning("chat_cache_commit_failed")
        await _rollback_session(session)


async def _store_semantic_cache(
    session: AsyncSession,
    settings: Settings,
    *,
    message: str,
    mailbox: str | None,
    user_key: str | None,
    embedding: list[float] | None,
    response: ChatAskResponse,
    intent: ChatIntent | None,
    bypass_cache: bool,
) -> None:
    if (
        bypass_cache
        or embedding is None
        or not settings.chat_semantic_cache_enabled
        or not (user_key or "").strip()
        or response.refused_write
        or _is_uncacheable_answer(response.answer)
        or _is_unverified_verdict(response.grounded_verifier)
    ):
        return
    try:
        await chat_cache_repo.store(
            session,
            mailbox_key=_mailbox_cache_key(mailbox, settings),
            user_key=user_key or "",
            query_normalized=chat_cache_repo.normalize_chat_query(message),
            query_embedding=embedding,
            response_json=response.model_dump(mode="json"),
            citation_thread_ids=[citation.thread_id for citation in response.citations],
            expires_at=datetime.now(UTC) + timedelta(seconds=_ttl_seconds(settings, intent)),
        )
    except Exception:
        logger.warning("chat_cache_store_failed", mailbox=mailbox)
        await _rollback_session(session)


def _meta_event(
    *,
    citations: list[ChatCitation],
    retrieval_count: int,
    mailbox: str | None,
    refused_write: bool,
    cached: bool = False,
    cache_similarity: float | None = None,
    grounded_verifier: str = "SKIPPED",
) -> dict:
    return {
        "type": "meta",
        "citations": [citation.model_dump(mode="json") for citation in citations],
        "retrieval_count": retrieval_count,
        "mailbox": mailbox,
        "refused_write": refused_write,
        "cached": cached,
        "cache_similarity": cache_similarity,
        "grounded_verifier": grounded_verifier,
    }


async def _retrieve_for_write(
    session: AsyncSession,
    settings: Settings,
    *,
    openai_client: AsyncOpenAI | None,
    message: str,
    mailbox: str | None,
    limit: int,
    history: list[ChatHistoryTurn],
) -> tuple[list[SearchHit], str | None]:
    search_query = retrieval_message(message, history)
    search = await search_service.search_threads(
        session,
        settings,
        openai_client=openai_client,
        query=search_query,
        mailbox=mailbox,
        limit=limit,
        mode="hybrid",
        allow_empty=True,
    )
    return search.hits, search.mailbox


async def ask(
    session: AsyncSession,
    settings: Settings,
    *,
    openai_client: AsyncOpenAI | None,
    anthropic_client: AsyncAnthropic,
    message: str,
    mailbox: str | None = None,
    limit: int | None = None,
    history: list[ChatHistoryTurn] | None = None,
    user_key: str | None = None,
    user_id: uuid.UUID | None = None,
    bypass_cache: bool = False,
    session_id: uuid.UUID | None = None,
) -> ChatAskResponse:
    cleaned = sanitize_user_text(message)
    refused_write = detect_write_intent(cleaned)
    retrieval_limit = CHAT_DEFAULT_LIMIT if limit is None else limit
    session_history = await _load_session_history(
        session,
        session_id=session_id,
        user_id=user_id,
    )
    prior = _cleaned_history(_merge_history(history, session_history))
    started = time.perf_counter()

    if refused_write:
        hits, resolved = await _retrieve_for_write(
            session,
            settings,
            openai_client=openai_client,
            message=cleaned,
            mailbox=mailbox,
            limit=retrieval_limit,
            history=prior,
        )
        citations = citations_from_hits(hits)
        return ChatAskResponse(
            answer=_write_refusal_answer(hits),
            citations=citations,
            retrieval_count=len(hits),
            mailbox=resolved,
            refused_write=True,
        )

    plan = classify_chat_intent(
        cleaned,
        history=prior,
        mailbox_emails=list(settings.mailbox_list),
    )
    if plan.intent == ChatIntent.OUT_OF_SCOPE:
        return _out_of_scope_response(mailbox=mailbox)

    cached: ChatAskResponse | None = None
    embedding: list[float] | None = None
    if intent_uses_semantic_cache(plan.intent):
        cached, embedding = await _lookup_semantic_cache(
            session,
            settings,
            openai_client=openai_client,
            message=cleaned,
            mailbox=mailbox,
            user_key=user_key,
            bypass_cache=bypass_cache,
        )
    if cached is not None:
        _log_chat_ask(
            hit_count=cached.retrieval_count,
            mailbox=mailbox,
            refused_write=False,
            query_length=len(cleaned),
            cached=True,
            cache_similarity=cached.cache_similarity,
            retrieval_count=cached.retrieval_count,
            grounded_verifier=cached.grounded_verifier,
            intent=plan.intent,
            tool_used_first=None,
            tool_iterations=0,
            ttft_ms=_elapsed_ms(started),
            total_ms=_elapsed_ms(started),
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=None,
            cache_write_tokens=None,
        )
        return cached

    tool_mailbox = mailbox or plan.mailbox
    question = _agent_question(cleaned, plan.thread_id)

    async def execute(name: str, arguments: dict):
        return await execute_chat_tool(
            session,
            settings,
            openai_client=openai_client,
            name=name,
            arguments=arguments,
            mailbox=tool_mailbox,
            limit=retrieval_limit,
        )

    result = await run_chat_agent(
        client=anthropic_client,
        settings=settings,
        question=question,
        execute_tool=execute,
        history=prior,
        initial_tool=plan.tool_name,
    )
    if result.hits:
        answer = (
            sanitize_chat_answer(
                result.answer,
                known_thread_ids={hit.thread_id for hit in result.hits},
            )
            if result.answer
            else NO_MATCH_ANSWER
        )
        citations = citations_from_hits(result.hits)
        count = len(result.hits)
        grounded = bool(result.answer)
    elif result.grounded and result.answer:
        answer = sanitize_chat_answer(result.answer)
        citations = []
        count = 0
        grounded = True
    else:
        answer = no_match_answer(mailbox=mailbox)
        citations = []
        count = 0
        grounded = False
    verdict = await _groundedness_verdict(
        settings=settings,
        client=anthropic_client,
        answer=answer,
        hits=result.hits,
        grounded=grounded,
    )
    response = ChatAskResponse(
        answer=answer,
        citations=citations,
        retrieval_count=count,
        mailbox=mailbox,
        refused_write=False,
        grounded_verifier=verdict,  # type: ignore[arg-type]
    )
    await _persist_session_turn(
        session,
        session_id=session_id,
        user_id=user_id,
        user_message=cleaned,
        assistant_message=answer,
        citations=citations,
    )
    await _store_semantic_cache(
        session,
        settings,
        message=cleaned,
        mailbox=mailbox,
        user_key=user_key,
        embedding=embedding,
        response=response,
        intent=plan.intent,
        bypass_cache=bypass_cache,
    )
    await _commit_session(session)
    _log_chat_ask(
        hit_count=count,
        mailbox=mailbox,
        refused_write=False,
        query_length=len(cleaned),
        intent=plan.intent,
        tool_used_first=result.tool_used_first or plan.tool_name,
        tool_iterations=result.tool_iterations,
        grounded_verifier=verdict,
        retrieval_count=count,
        cached=False,
        cache_similarity=None,
        ttft_ms=result.ttft_ms,
        total_ms=_elapsed_ms(started),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_write_tokens=result.cache_write_tokens,
    )
    return response


async def iter_ask_events(
    session: AsyncSession,
    settings: Settings,
    *,
    openai_client: AsyncOpenAI | None,
    anthropic_client: AsyncAnthropic,
    message: str,
    mailbox: str | None = None,
    limit: int | None = None,
    history: list[ChatHistoryTurn] | None = None,
    user_key: str | None = None,
    user_id: uuid.UUID | None = None,
    bypass_cache: bool = False,
    session_id: uuid.UUID | None = None,
) -> AsyncIterator[dict]:
    """SSE payload dicts: status, meta, zero or more deltas, then done."""
    cleaned = sanitize_user_text(message)
    refused_write = detect_write_intent(cleaned)
    retrieval_limit = CHAT_DEFAULT_LIMIT if limit is None else limit
    session_history = await _load_session_history(
        session,
        session_id=session_id,
        user_id=user_id,
    )
    prior = _cleaned_history(_merge_history(history, session_history))
    started = time.perf_counter()

    if refused_write:
        hits, resolved = await _retrieve_for_write(
            session,
            settings,
            openai_client=openai_client,
            message=cleaned,
            mailbox=mailbox,
            limit=retrieval_limit,
            history=prior,
        )
        citations = citations_from_hits(hits)
        yield _meta_event(
            citations=citations,
            retrieval_count=len(hits),
            mailbox=resolved,
            refused_write=True,
        )
        yield {"type": "delta", "text": _write_refusal_answer(hits)}
        yield {"type": "done", "grounded_verifier": "SKIPPED"}
        return

    plan = classify_chat_intent(
        cleaned,
        history=prior,
        mailbox_emails=list(settings.mailbox_list),
    )
    if plan.intent == ChatIntent.OUT_OF_SCOPE:
        yield _meta_event(
            citations=[],
            retrieval_count=0,
            mailbox=mailbox,
            refused_write=False,
        )
        yield {"type": "delta", "text": OUT_OF_SCOPE_ANSWER}
        yield {"type": "done", "grounded_verifier": "SKIPPED"}
        return

    cached: ChatAskResponse | None = None
    embedding: list[float] | None = None
    if intent_uses_semantic_cache(plan.intent):
        cached, embedding = await _lookup_semantic_cache(
            session,
            settings,
            openai_client=openai_client,
            message=cleaned,
            mailbox=mailbox,
            user_key=user_key,
            bypass_cache=bypass_cache,
        )
    if cached is not None:
        _log_chat_ask(
            hit_count=cached.retrieval_count,
            mailbox=mailbox,
            refused_write=False,
            query_length=len(cleaned),
            cached=True,
            cache_similarity=cached.cache_similarity,
            retrieval_count=cached.retrieval_count,
            grounded_verifier=cached.grounded_verifier,
            intent=plan.intent,
            tool_iterations=0,
            ttft_ms=_elapsed_ms(started),
            total_ms=_elapsed_ms(started),
            input_tokens=0,
            output_tokens=0,
        )
        yield _meta_event(
            citations=list(cached.citations),
            retrieval_count=cached.retrieval_count,
            mailbox=cached.mailbox,
            refused_write=False,
            cached=True,
            cache_similarity=cached.cache_similarity,
            grounded_verifier=cached.grounded_verifier,
        )
        yield {"type": "delta", "text": cached.answer}
        yield {"type": "done", "grounded_verifier": cached.grounded_verifier}
        return

    yield {"type": "status", "text": STATUS_SEARCHING}

    tool_mailbox = mailbox or plan.mailbox
    question = _agent_question(cleaned, plan.thread_id)

    async def execute(name: str, arguments: dict):
        return await execute_chat_tool(
            session,
            settings,
            openai_client=openai_client,
            name=name,
            arguments=arguments,
            mailbox=tool_mailbox,
            limit=retrieval_limit,
        )

    answer = ""
    hits: list[SearchHit] = []
    grounded = False
    forwarded_delta = False
    meta_sent = False
    drafting_sent = False
    delta_scrubber = ChatDeltaScrubber()
    tool_used_first: str | None = None
    tool_iterations = 0
    ttft_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    try:
        async for event in iter_chat_agent(
            client=anthropic_client,
            settings=settings,
            question=question,
            execute_tool=execute,
            history=prior,
            initial_tool=plan.tool_name,
        ):
            if event.get("type") == "status":
                yield {"type": "status", "text": event.get("text") or ""}
            elif event.get("type") == "retrieved":
                hits = list(event.get("hits") or [])
                delta_scrubber.known_thread_ids = {hit.thread_id for hit in hits}
                if hits:
                    yield {"type": "status", "text": _reading_status(len(hits))}
                if not meta_sent:
                    yield _meta_event(
                        citations=citations_from_hits(hits),
                        retrieval_count=len(hits),
                        mailbox=mailbox,
                        refused_write=False,
                    )
                    meta_sent = True
                if not drafting_sent:
                    yield {"type": "status", "text": STATUS_DRAFTING}
                    drafting_sent = True
            elif event.get("type") == "delta":
                piece = str(event.get("text") or "")
                if not piece:
                    continue
                if not drafting_sent:
                    yield {"type": "status", "text": STATUS_DRAFTING}
                    drafting_sent = True
                if not meta_sent:
                    yield _meta_event(
                        citations=citations_from_hits(hits),
                        retrieval_count=len(hits),
                        mailbox=mailbox,
                        refused_write=False,
                    )
                    meta_sent = True
                if ttft_ms is None:
                    ttft_ms = _elapsed_ms(started)
                if hits:
                    delta_scrubber.known_thread_ids = {hit.thread_id for hit in hits}
                text = delta_scrubber.feed(piece)
                if not text:
                    continue
                forwarded_delta = True
                yield {"type": "delta", "text": text}
            elif event.get("type") == "result":
                answer = str(event.get("answer") or "")
                hits = list(event.get("hits") or [])
                grounded = bool(event.get("grounded"))
                tool_used_first = event.get("tool_used_first") or plan.tool_name
                tool_iterations = int(event.get("tool_iterations") or 0)
                if event.get("ttft_ms") is not None:
                    ttft_ms = int(event["ttft_ms"])
                input_tokens = event.get("input_tokens")
                output_tokens = event.get("output_tokens")
                cache_read_tokens = event.get("cache_read_tokens")
                cache_write_tokens = event.get("cache_write_tokens")
    except ChatError:
        if forwarded_delta:
            yield {
                "type": "error",
                "message": "Claude chat failed",
                "partial": True,
            }
            return
        raise
    except Exception:
        logger.exception("chat_ask_events_failed")
        if forwarded_delta:
            yield {
                "type": "error",
                "message": "Claude chat failed",
                "partial": True,
            }
            return
        raise ChatError("Claude chat failed") from None

    leftover = delta_scrubber.flush()
    if leftover:
        forwarded_delta = True
        yield {"type": "delta", "text": leftover}

    if hits:
        citations = citations_from_hits(hits)
        text = (
            sanitize_chat_answer(
                answer,
                known_thread_ids={hit.thread_id for hit in hits},
            )
            if answer
            else NO_MATCH_ANSWER
        )
        count = len(hits)
        grounded_for_verify = bool(answer)
    elif grounded and answer:
        citations = []
        text = sanitize_chat_answer(answer)
        count = 0
        grounded_for_verify = True
    else:
        citations = []
        text = no_match_answer(mailbox=mailbox)
        count = 0
        grounded_for_verify = False
    verdict = await _groundedness_verdict(
        settings=settings,
        client=anthropic_client,
        answer=text,
        hits=hits,
        grounded=grounded_for_verify,
    )
    # H1: the verdict is only known after the agent loop finishes, but a meta
    # event carrying (possibly identical) citations was already sent eagerly
    # inside the loop the moment the first delta/retrieved hits arrived — the
    # client needs that early to render citations before the loop completes.
    # Do not send a second "meta" here; the terminal "done" event below
    # already carries grounded_verifier, which is all a second meta would add.
    if not meta_sent:
        yield _meta_event(
            citations=citations,
            retrieval_count=count,
            mailbox=mailbox,
            refused_write=False,
            grounded_verifier=verdict,
        )
    await _persist_session_turn(
        session,
        session_id=session_id,
        user_id=user_id,
        user_message=cleaned,
        assistant_message=text,
        citations=citations,
    )
    await _store_semantic_cache(
        session,
        settings,
        message=cleaned,
        mailbox=mailbox,
        user_key=user_key,
        embedding=embedding,
        response=ChatAskResponse(
            answer=text,
            citations=citations,
            retrieval_count=count,
            mailbox=mailbox,
            refused_write=False,
            grounded_verifier=verdict,  # type: ignore[arg-type]
        ),
        intent=plan.intent,
        bypass_cache=bypass_cache,
    )
    await _commit_session(session)
    _log_chat_ask(
        hit_count=count,
        mailbox=mailbox,
        refused_write=False,
        query_length=len(cleaned),
        intent=plan.intent,
        tool_used_first=tool_used_first or plan.tool_name,
        tool_iterations=tool_iterations,
        grounded_verifier=verdict,
        retrieval_count=count,
        cached=False,
        ttft_ms=ttft_ms,
        total_ms=_elapsed_ms(started),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )
    if not forwarded_delta:
        yield {"type": "delta", "text": text}
    yield {"type": "done", "grounded_verifier": verdict}
