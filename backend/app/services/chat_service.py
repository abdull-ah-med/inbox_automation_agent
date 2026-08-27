"""Read-only NL chat: tool-mediated RAG, then a grounded answer with citations.

Never sends, approves, rejects, or writes mail. Citations are always built from
tool hits, never from model-invented thread ids.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ChatError
from app.core.sanitize import sanitize_user_text
from app.llm.chat import (
    ChatAgentResult,
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
from app.llm.chat_tools import search_results_from_hits
from app.llm.groundedness import verify_grounded
from app.llm.pii_redact import scrub_text
from app.models.db.thread import Thread
from app.models.schemas.chat import (
    CHAT_DEFAULT_LIMIT,
    ChatAskResponse,
    ChatCitation,
    ChatHistoryTurn,
    GroundedVerifier,
)
from app.models.schemas.search import SearchHit
from app.repositories import chat_cache_repo
from app.services import chat_session_service, embedding_service, search_service
from app.services.chat_intent import ChatIntent, ChatIntentPlan, classify_chat_intent
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

_WRITE_RE = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+)?("
    + "|".join(re.escape(phrase) for phrase in _WRITE_PHRASES)
    + r")\b",
    re.IGNORECASE,
)


STATUS_SEARCHING = "Searching your inbox…"
STATUS_DRAFTING = "Drafting answer…"


def detect_write_intent(message: str) -> bool:
    """Command-shaped send/approve/reject/delete/junk asks, not mid-sentence mentions."""
    lowered = " ".join((message or "").lower().split())
    return _WRITE_RE.search(lowered) is not None


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
        if turn.role == "assistant":
            text = scrub_text(sanitize_user_text(turn.content))
        else:
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
    evidence: Sequence[object] | None = None,
    mailbox: str | None = None,
    user_id: uuid.UUID | None = None,
) -> GroundedVerifier:
    if not settings.chat_groundedness_enabled or not grounded:
        return "SKIPPED"
    packed: Sequence[object]
    if evidence:
        packed = evidence
    elif hits:
        packed = search_results_from_hits(hits)
    else:
        packed = []
    result = await verify_grounded(
        answer,
        packed,
        client=client,
        settings=settings,
    )
    if result.verdict == "UNSUPPORTED":
        logger.warning(
            "chat.groundedness_unsupported",
            unsupported_spans=result.unsupported_spans,
            mailbox=mailbox,
            user_id=str(user_id) if user_id is not None else None,
        )
    return result.verdict


_STALE_CACHE_STATES = frozenset({"SPAM"})


async def _citations_still_current(
    session: AsyncSession,
    hit: chat_cache_repo.ChatCacheHit,
    *,
    mailbox: str | None,
    settings: Settings,
) -> bool:
    ids = list(hit.citation_thread_ids or [])
    if not ids:
        return True
    result = await session.execute(
        select(Thread.id, Thread.mailbox, Thread.state).where(Thread.id.in_(ids))
    )
    found = {row.id: row for row in result.all()}
    if len(found) != len(set(ids)):
        return False
    allowed = {item.strip().lower() for item in settings.mailbox_list}
    scoped = (mailbox or "").strip().lower()
    for thread_id in ids:
        row = found[thread_id]
        if row.state in _STALE_CACHE_STATES:
            return False
        box = (row.mailbox or "").strip().lower()
        if scoped and box != scoped:
            return False
        if allowed and box not in allowed:
            return False
    return True


def _mailbox_cache_key(mailbox: str | None, settings: Settings) -> str:
    return chat_cache_repo.scope_key(mailbox, mailbox_list=settings.mailbox_list)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


@dataclass(frozen=True)
class ChatObservation:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    ttft_ms: int | None = None
    total_ms: int | None = None
    verdict: str | None = None


def _log_chat_ask(
    *,
    hit_count: int,
    mailbox: str | None,
    user_id: uuid.UUID | None = None,
    refused_write: bool,
    query_length: int,
    observation: ChatObservation,
    intent: object = None,
    tool_used_first: str | None = None,
    tool_iterations: int | None = None,
    retrieval_count: int | None = None,
    cached: bool = False,
    cache_similarity: float | None = None,
) -> None:
    logger.info(
        "chat.ask",
        hit_count=hit_count,
        mailbox=mailbox,
        user_id=str(user_id) if user_id is not None else None,
        refused_write=refused_write,
        query_length=query_length,
        intent=intent,
        tool_used_first=tool_used_first,
        tool_iterations=tool_iterations,
        retrieval_count=retrieval_count,
        cached=cached,
        cache_similarity=cache_similarity,
        grounded_verifier=observation.verdict,
        ttft_ms=observation.ttft_ms,
        total_ms=observation.total_ms,
        input_tokens=observation.input_tokens,
        output_tokens=observation.output_tokens,
        cache_read_tokens=observation.cache_read_tokens,
        cache_write_tokens=observation.cache_write_tokens,
    )


def _is_uncacheable_answer(answer: str) -> bool:
    text = (answer or "").strip()
    if not text:
        return True
    if text in {NO_MATCH_ANSWER, OUT_OF_SCOPE_ANSWER, WRITE_REFUSAL_ANSWER}:
        return True
    if text.startswith((WRITE_REFUSAL_ANSWER, OUT_OF_SCOPE_ANSWER)):
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
    if not await _citations_still_current(session, hit, mailbox=mailbox, settings=settings):
        logger.info("chat_cache_hit_stale_citations", mailbox=mailbox)
        try:
            await chat_cache_repo.invalidate_for_threads(
                session, list(hit.citation_thread_ids or [])
            )
            await _commit_session(session)
        except Exception:
            logger.warning("chat_cache_stale_citation_invalidate_failed", mailbox=mailbox)
            await _rollback_session(session)
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


@dataclass
class ChatAskPipeline:
    """Shared phases for ask() and iter_ask_events() (sanitize → cache → agent → store)."""

    session: AsyncSession
    settings: Settings
    openai_client: AsyncOpenAI | None
    anthropic_client: AsyncAnthropic
    message: str
    mailbox: str | None = None
    limit: int | None = None
    history: list[ChatHistoryTurn] | None = None
    user_key: str | None = None
    user_id: uuid.UUID | None = None
    bypass_cache: bool = False
    session_id: uuid.UUID | None = None
    cleaned: str = field(init=False, default="")
    retrieval_limit: int = field(init=False, default=CHAT_DEFAULT_LIMIT)
    prior: list[ChatHistoryTurn] = field(init=False, default_factory=list)
    started: float = field(init=False, default=0.0)
    plan: ChatIntentPlan | None = field(init=False, default=None)
    embedding: list[float] | None = field(init=False, default=None)
    tool_mailbox: str | None = field(init=False, default=None)
    question: str = field(init=False, default="")

    async def setup(self) -> None:
        self.cleaned = sanitize_user_text(self.message)
        self.retrieval_limit = CHAT_DEFAULT_LIMIT if self.limit is None else self.limit
        session_history = await _load_session_history(
            self.session,
            session_id=self.session_id,
            user_id=self.user_id,
        )
        self.prior = _cleaned_history(_merge_history(self.history, session_history))
        self.started = time.perf_counter()

    async def early_response(self) -> ChatAskResponse | None:
        if detect_write_intent(self.cleaned):
            return await self._write_refusal()
        self.plan = classify_chat_intent(
            self.cleaned,
            history=self.prior,
            mailbox_emails=list(self.settings.mailbox_list),
        )
        if self.plan.intent == ChatIntent.OUT_OF_SCOPE:
            return _out_of_scope_response(mailbox=self.mailbox)
        return await self._cached_response()

    async def _write_refusal(self) -> ChatAskResponse:
        hits, resolved = await _retrieve_for_write(
            self.session,
            self.settings,
            openai_client=self.openai_client,
            message=self.cleaned,
            mailbox=self.mailbox,
            limit=self.retrieval_limit,
            history=self.prior,
        )
        return ChatAskResponse(
            answer=_write_refusal_answer(hits),
            citations=citations_from_hits(hits),
            retrieval_count=len(hits),
            mailbox=resolved,
            refused_write=True,
        )

    async def _cached_response(self) -> ChatAskResponse | None:
        if self.plan is None:
            raise RuntimeError("chat plan not classified")
        if not intent_uses_semantic_cache(self.plan.intent):
            return None
        cached, embedding = await _lookup_semantic_cache(
            self.session,
            self.settings,
            openai_client=self.openai_client,
            message=self.cleaned,
            mailbox=self.mailbox,
            user_key=self.user_key,
            bypass_cache=self.bypass_cache,
        )
        self.embedding = embedding
        if cached is None:
            return None
        elapsed = _elapsed_ms(self.started)
        _log_chat_ask(
            hit_count=cached.retrieval_count,
            mailbox=self.mailbox,
            user_id=self.user_id,
            refused_write=False,
            query_length=len(self.cleaned),
            observation=ChatObservation(
                input_tokens=0,
                output_tokens=0,
                ttft_ms=elapsed,
                total_ms=elapsed,
                verdict=cached.grounded_verifier,
            ),
            cached=True,
            cache_similarity=cached.cache_similarity,
            retrieval_count=cached.retrieval_count,
            intent=self.plan.intent,
            tool_used_first=None,
            tool_iterations=0,
        )
        return cached

    def prepare_agent(self) -> None:
        if self.plan is None:
            raise RuntimeError("chat plan not classified")
        self.tool_mailbox = self.mailbox or self.plan.mailbox
        self.question = _agent_question(self.cleaned, self.plan.thread_id)

    async def execute_tool(self, name: str, arguments: dict) -> object:
        return await execute_chat_tool(
            self.session,
            self.settings,
            openai_client=self.openai_client,
            name=name,
            arguments=arguments,
            mailbox=self.tool_mailbox,
            limit=self.retrieval_limit,
        )

    def compose_answer(
        self,
        *,
        answer: str,
        hits: list[SearchHit],
        grounded: bool,
    ) -> tuple[str, list[ChatCitation], int, bool]:
        if hits:
            text = (
                sanitize_chat_answer(
                    answer,
                    known_thread_ids={hit.thread_id for hit in hits},
                    mailbox=self.mailbox,
                    user_id=self.user_id,
                )
                if answer
                else NO_MATCH_ANSWER
            )
            return text, citations_from_hits(hits), len(hits), bool(answer)
        if grounded and answer:
            return (
                sanitize_chat_answer(
                    answer,
                    mailbox=self.mailbox,
                    user_id=self.user_id,
                ),
                [],
                0,
                True,
            )
        return no_match_answer(mailbox=self.mailbox), [], 0, False

    async def finish(
        self,
        *,
        answer: str,
        citations: list[ChatCitation],
        count: int,
        verdict: GroundedVerifier | str,
        hits: list[SearchHit],
        grounded: bool,
        evidence: Sequence[object] | None,
        tool_used_first: str | None,
        tool_iterations: int,
        ttft_ms: int | None,
        input_tokens: int | None,
        output_tokens: int | None,
        cache_read_tokens: int | None,
        cache_write_tokens: int | None,
    ) -> ChatAskResponse:
        if verdict == "":
            verdict = await _groundedness_verdict(
                settings=self.settings,
                client=self.anthropic_client,
                answer=answer,
                hits=hits,
                grounded=grounded,
                evidence=evidence,
                mailbox=self.mailbox,
                user_id=self.user_id,
            )
        if self.plan is None:
            raise RuntimeError("chat plan not classified")
        response = ChatAskResponse(
            answer=answer,
            citations=citations,
            retrieval_count=count,
            mailbox=self.mailbox,
            refused_write=False,
            grounded_verifier=verdict if verdict != "" else "SKIPPED",
        )
        await _persist_session_turn(
            self.session,
            session_id=self.session_id,
            user_id=self.user_id,
            user_message=self.cleaned,
            assistant_message=answer,
            citations=citations,
        )
        await _store_semantic_cache(
            self.session,
            self.settings,
            message=self.cleaned,
            mailbox=self.mailbox,
            user_key=self.user_key,
            embedding=self.embedding,
            response=response,
            intent=self.plan.intent,
            bypass_cache=self.bypass_cache,
        )
        await _commit_session(self.session)
        _log_chat_ask(
            hit_count=count,
            mailbox=self.mailbox,
            user_id=self.user_id,
            refused_write=False,
            query_length=len(self.cleaned),
            observation=ChatObservation(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                ttft_ms=ttft_ms,
                total_ms=_elapsed_ms(self.started),
                verdict=verdict,
            ),
            intent=self.plan.intent,
            tool_used_first=tool_used_first or self.plan.tool_name,
            tool_iterations=tool_iterations,
            retrieval_count=count,
            cached=False,
            cache_similarity=None,
        )
        return response

    async def complete_agent_result(self, result: ChatAgentResult) -> ChatAskResponse:
        answer, citations, count, grounded = self.compose_answer(
            answer=result.answer,
            hits=result.hits,
            grounded=result.grounded,
        )
        return await self.finish(
            answer=answer,
            citations=citations,
            count=count,
            verdict="",
            hits=result.hits,
            grounded=grounded,
            evidence=result.evidence_blocks,
            tool_used_first=result.tool_used_first,
            tool_iterations=result.tool_iterations,
            ttft_ms=result.ttft_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
        )

    def events_for(self, response: ChatAskResponse) -> list[dict]:
        return [
            _meta_event(
                citations=list(response.citations),
                retrieval_count=response.retrieval_count,
                mailbox=response.mailbox,
                refused_write=response.refused_write,
                cached=response.cached,
                cache_similarity=response.cache_similarity,
                grounded_verifier=response.grounded_verifier,
            ),
            {"type": "delta", "text": response.answer},
            {"type": "done", "grounded_verifier": response.grounded_verifier},
        ]


def _pipeline(
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
) -> ChatAskPipeline:
    return ChatAskPipeline(
        session=session,
        settings=settings,
        openai_client=openai_client,
        anthropic_client=anthropic_client,
        message=message,
        mailbox=mailbox,
        limit=limit,
        history=history,
        user_key=user_key,
        user_id=user_id,
        bypass_cache=bypass_cache,
        session_id=session_id,
    )


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
    pipe = _pipeline(
        session,
        settings,
        openai_client=openai_client,
        anthropic_client=anthropic_client,
        message=message,
        mailbox=mailbox,
        limit=limit,
        history=history,
        user_key=user_key,
        user_id=user_id,
        bypass_cache=bypass_cache,
        session_id=session_id,
    )
    await pipe.setup()
    early = await pipe.early_response()
    if early is not None:
        return early
    pipe.prepare_agent()
    result = await run_chat_agent(
        client=anthropic_client,
        settings=settings,
        question=pipe.question,
        execute_tool=pipe.execute_tool,
        history=pipe.prior,
        initial_tool=pipe.plan.tool_name if pipe.plan else None,
        mailbox=pipe.mailbox,
        user_id=pipe.user_id,
    )
    return await pipe.complete_agent_result(result)


@dataclass
class _AskStreamState:
    answer: str = ""
    hits: list = field(default_factory=list)
    grounded: bool = False
    evidence_blocks: list = field(default_factory=list)
    forwarded_delta: bool = False
    meta_sent: bool = False
    drafting_sent: bool = False
    tool_used_first: str | None = None
    tool_iterations: int = 0
    ttft_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


def _ask_retrieved_events(
    state: _AskStreamState,
    event: dict,
    *,
    delta_scrubber: ChatDeltaScrubber,
    mailbox: str | None,
) -> list[dict]:
    out: list[dict] = []
    state.hits = list(event.get("hits") or [])
    delta_scrubber.known_thread_ids = {hit.thread_id for hit in state.hits}
    if state.hits:
        out.append({"type": "status", "text": _reading_status(len(state.hits))})
    if not state.meta_sent:
        out.append(
            _meta_event(
                citations=citations_from_hits(state.hits),
                retrieval_count=len(state.hits),
                mailbox=mailbox,
                refused_write=False,
            )
        )
        state.meta_sent = True
    if not state.drafting_sent:
        out.append({"type": "status", "text": STATUS_DRAFTING})
        state.drafting_sent = True
    return out


def _ask_delta_events(
    state: _AskStreamState,
    event: dict,
    *,
    delta_scrubber: ChatDeltaScrubber,
    mailbox: str | None,
    started: float,
) -> list[dict]:
    out: list[dict] = []
    piece = str(event.get("text") or "")
    if not piece:
        return out
    if not state.drafting_sent:
        out.append({"type": "status", "text": STATUS_DRAFTING})
        state.drafting_sent = True
    if not state.meta_sent:
        out.append(
            _meta_event(
                citations=citations_from_hits(state.hits),
                retrieval_count=len(state.hits),
                mailbox=mailbox,
                refused_write=False,
            )
        )
        state.meta_sent = True
    if state.ttft_ms is None:
        state.ttft_ms = _elapsed_ms(started)
    if state.hits:
        delta_scrubber.known_thread_ids = {hit.thread_id for hit in state.hits}
    text = delta_scrubber.feed(piece)
    if not text:
        return out
    state.forwarded_delta = True
    out.append({"type": "delta", "text": text})
    return out


def _ask_apply_result(state: _AskStreamState, event: dict, *, plan_tool: str | None) -> None:
    state.answer = str(event.get("answer") or "")
    state.hits = list(event.get("hits") or [])
    state.grounded = bool(event.get("grounded"))
    state.evidence_blocks = list(event.get("evidence_blocks") or [])
    state.tool_used_first = event.get("tool_used_first") or plan_tool
    state.tool_iterations = int(event.get("tool_iterations") or 0)
    if event.get("ttft_ms") is not None:
        state.ttft_ms = int(event["ttft_ms"])
    state.input_tokens = event.get("input_tokens")
    state.output_tokens = event.get("output_tokens")
    state.cache_read_tokens = event.get("cache_read_tokens")
    state.cache_write_tokens = event.get("cache_write_tokens")


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
    pipe = _pipeline(
        session,
        settings,
        openai_client=openai_client,
        anthropic_client=anthropic_client,
        message=message,
        mailbox=mailbox,
        limit=limit,
        history=history,
        user_key=user_key,
        user_id=user_id,
        bypass_cache=bypass_cache,
        session_id=session_id,
    )
    await pipe.setup()
    early = await pipe.early_response()
    if early is not None:
        for event in pipe.events_for(early):
            yield event
        return

    yield {"type": "status", "text": STATUS_SEARCHING}
    pipe.prepare_agent()

    state = _AskStreamState()
    delta_scrubber = ChatDeltaScrubber(
        mailbox=pipe.mailbox,
        user_id=pipe.user_id,
    )
    plan_tool = pipe.plan.tool_name if pipe.plan else None
    try:
        async for event in iter_chat_agent(
            client=anthropic_client,
            settings=settings,
            question=pipe.question,
            execute_tool=pipe.execute_tool,
            history=pipe.prior,
            initial_tool=plan_tool,
            mailbox=pipe.mailbox,
            user_id=pipe.user_id,
        ):
            etype = event.get("type")
            if etype == "status":
                yield {"type": "status", "text": event.get("text") or ""}
            elif etype == "retrieved":
                for item in _ask_retrieved_events(
                    state, event, delta_scrubber=delta_scrubber, mailbox=mailbox
                ):
                    yield item
            elif etype == "delta":
                for item in _ask_delta_events(
                    state,
                    event,
                    delta_scrubber=delta_scrubber,
                    mailbox=mailbox,
                    started=pipe.started,
                ):
                    yield item
            elif etype == "result":
                _ask_apply_result(state, event, plan_tool=plan_tool)
    except ChatError:
        if state.forwarded_delta:
            yield {
                "type": "error",
                "message": "Claude chat failed",
                "partial": True,
            }
            return
        raise
    except Exception:
        logger.exception(
            "chat_ask_events_failed",
            mailbox=pipe.mailbox,
            user_id=str(pipe.user_id) if pipe.user_id is not None else None,
        )
        if state.forwarded_delta:
            yield {
                "type": "error",
                "message": "Claude chat failed",
                "partial": True,
            }
            return
        raise ChatError("Claude chat failed") from None

    leftover = delta_scrubber.flush()
    if leftover:
        state.forwarded_delta = True
        yield {"type": "delta", "text": leftover}

    text, citations, count, grounded_for_verify = pipe.compose_answer(
        answer=state.answer,
        hits=state.hits,
        grounded=state.grounded,
    )
    response = await pipe.finish(
        answer=text,
        citations=citations,
        count=count,
        verdict="",
        hits=state.hits,
        grounded=grounded_for_verify,
        evidence=state.evidence_blocks,
        tool_used_first=state.tool_used_first,
        tool_iterations=state.tool_iterations,
        ttft_ms=state.ttft_ms,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        cache_read_tokens=state.cache_read_tokens,
        cache_write_tokens=state.cache_write_tokens,
    )
    # H1: do not send a second meta; terminal done carries grounded_verifier.
    if not state.meta_sent:
        yield _meta_event(
            citations=citations,
            retrieval_count=count,
            mailbox=mailbox,
            refused_write=False,
            grounded_verifier=response.grounded_verifier,
        )
    if not state.forwarded_delta:
        yield {"type": "delta", "text": text}
    yield {"type": "done", "grounded_verifier": response.grounded_verifier}
