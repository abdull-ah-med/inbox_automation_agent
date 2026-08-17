"""Read-only NL chat: retrieve via hybrid search, then answer with citations.

Never sends, approves, rejects, or writes mail. Citations are always built from
search hits, never from model-invented thread ids.
"""

from __future__ import annotations

import uuid

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.chat import generate_chat_answer
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
    cleaned = (message or "").strip()
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

    if refused_write:
        return ChatAskResponse(
            answer=_write_refusal_answer(hits),
            citations=citations,
            retrieval_count=len(hits),
            mailbox=search.mailbox,
            refused_write=True,
        )

    if not hits:
        return ChatAskResponse(
            answer=NO_MATCH_ANSWER,
            citations=[],
            retrieval_count=0,
            mailbox=search.mailbox,
            refused_write=False,
        )

    answer = await generate_chat_answer(
        client=anthropic_client,
        settings=settings,
        question=cleaned,
        hits=hits,
    )
    return ChatAskResponse(
        answer=answer,
        citations=citations,
        retrieval_count=len(hits),
        mailbox=search.mailbox,
            refused_write=False,
        )
