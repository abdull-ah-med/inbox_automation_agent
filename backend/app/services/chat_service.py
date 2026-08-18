"""Read-only NL chat: retrieve via hybrid search, then answer with citations.

Never sends, approves, rejects, or writes mail. Citations are always built from
search hits, never from model-invented thread ids.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.sanitize import sanitize_user_text
from app.llm.chat import generate_chat_answer, sanitize_chat_answer, stream_chat_answer
from app.llm.chat_prompts import NO_MATCH_ANSWER, WRITE_REFUSAL_ANSWER
from app.models.schemas.chat import (
    CHAT_DEFAULT_LIMIT,
    ChatAskResponse,
    ChatCitation,
)
from app.models.schemas.search import SearchHit
from app.services import search_service

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


def detect_write_intent(message: str) -> bool:
    """Light phrase match for send/approve/reject/delete/move-to-junk asks."""
    lowered = " ".join((message or "").lower().split())
    return any(phrase in lowered for phrase in _WRITE_PHRASES)


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


@dataclass(frozen=True)
class _AskPlan:
    question: str
    hits: list[SearchHit]
    citations: list[ChatCitation]
    mailbox: str | None
    refused_write: bool
    canned_answer: str | None
    retrieval_count: int


async def _prepare_ask(
    session: AsyncSession,
    settings: Settings,
    *,
    openai_client: AsyncOpenAI | None,
    message: str,
    mailbox: str | None,
    limit: int | None,
) -> _AskPlan:
    cleaned = sanitize_user_text(message)
    refused_write = detect_write_intent(cleaned)
    retrieval_limit = CHAT_DEFAULT_LIMIT if limit is None else limit

    search = await search_service.search_threads(
        session,
        settings,
        openai_client=openai_client,
        query=cleaned,
        mailbox=mailbox,
        limit=retrieval_limit,
        mode="hybrid",
    )
    hits = search.hits
    citations = citations_from_hits(hits)

    logger.info(
        "chat.ask",
        hit_count=len(hits),
        mailbox=search.mailbox,
        refused_write=refused_write,
        query_length=len(cleaned),
    )

    canned: str | None = None
    retrieval_count = len(hits)
    if refused_write:
        canned = _write_refusal_answer(hits)
    elif not hits:
        canned = NO_MATCH_ANSWER
        citations = []
        retrieval_count = 0

    return _AskPlan(
        question=cleaned,
        hits=hits,
        citations=citations,
        mailbox=search.mailbox,
        refused_write=refused_write,
        canned_answer=canned,
        retrieval_count=retrieval_count,
    )


def _meta_event(plan: _AskPlan) -> dict:
    return {
        "type": "meta",
        "citations": [citation.model_dump(mode="json") for citation in plan.citations],
        "retrieval_count": plan.retrieval_count,
        "mailbox": plan.mailbox,
        "refused_write": plan.refused_write,
    }


async def ask(
    session: AsyncSession,
    settings: Settings,
    *,
    openai_client: AsyncOpenAI | None,
    anthropic_client: AsyncAnthropic,
    message: str,
    mailbox: str | None = None,
    limit: int | None = None,
) -> ChatAskResponse:
    plan = await _prepare_ask(
        session,
        settings,
        openai_client=openai_client,
        message=message,
        mailbox=mailbox,
        limit=limit,
    )
    if plan.canned_answer is not None:
        return ChatAskResponse(
            answer=plan.canned_answer,
            citations=plan.citations,
            retrieval_count=plan.retrieval_count,
            mailbox=plan.mailbox,
            refused_write=plan.refused_write,
        )

    answer = await generate_chat_answer(
        client=anthropic_client,
        settings=settings,
        question=plan.question,
        hits=plan.hits,
    )
    return ChatAskResponse(
        answer=sanitize_chat_answer(answer),
        citations=plan.citations,
        retrieval_count=plan.retrieval_count,
        mailbox=plan.mailbox,
        refused_write=False,
    )


async def iter_ask_events(
    session: AsyncSession,
    settings: Settings,
    *,
    openai_client: AsyncOpenAI | None,
    anthropic_client: AsyncAnthropic,
    message: str,
    mailbox: str | None = None,
    limit: int | None = None,
) -> AsyncIterator[dict]:
    """SSE payload dicts: meta, zero or more deltas, then done."""
    plan = await _prepare_ask(
        session,
        settings,
        openai_client=openai_client,
        message=message,
        mailbox=mailbox,
        limit=limit,
    )
    yield _meta_event(plan)
    if plan.canned_answer is not None:
        yield {"type": "delta", "text": plan.canned_answer}
        yield {"type": "done"}
        return

    parts: list[str] = []
    async for chunk in stream_chat_answer(
        client=anthropic_client,
        settings=settings,
        question=plan.question,
        hits=plan.hits,
    ):
        parts.append(chunk)
    yield {"type": "delta", "text": sanitize_chat_answer("".join(parts))}
    yield {"type": "done"}
