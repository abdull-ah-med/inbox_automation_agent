"""Read-only NL chat: tool-mediated RAG, then a grounded answer with citations.

Never sends, approves, rejects, or writes mail. Citations are always built from
tool hits, never from model-invented thread ids.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.sanitize import sanitize_user_text
from app.llm.chat import iter_chat_agent, run_chat_agent, sanitize_chat_answer
from app.llm.chat_prompts import NO_MATCH_ANSWER, WRITE_REFUSAL_ANSWER
from app.models.schemas.chat import (
    CHAT_DEFAULT_LIMIT,
    ChatAskResponse,
    ChatCitation,
    ChatHistoryTurn,
)
from app.models.schemas.search import SearchHit
from app.services import search_service
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


def _meta_event(
    *,
    citations: list[ChatCitation],
    retrieval_count: int,
    mailbox: str | None,
    refused_write: bool,
) -> dict:
    return {
        "type": "meta",
        "citations": [citation.model_dump(mode="json") for citation in citations],
        "retrieval_count": retrieval_count,
        "mailbox": mailbox,
        "refused_write": refused_write,
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
) -> ChatAskResponse:
    cleaned = sanitize_user_text(message)
    refused_write = detect_write_intent(cleaned)
    retrieval_limit = CHAT_DEFAULT_LIMIT if limit is None else limit
    prior = _cleaned_history(history)

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

    async def execute(name: str, arguments: dict):
        return await execute_chat_tool(
            session,
            settings,
            openai_client=openai_client,
            name=name,
            arguments=arguments,
            mailbox=mailbox,
            limit=retrieval_limit,
        )

    result = await run_chat_agent(
        client=anthropic_client,
        settings=settings,
        question=cleaned,
        execute_tool=execute,
        history=prior,
    )
    logger.info(
        "chat.ask",
        hit_count=len(result.hits),
        mailbox=mailbox,
        refused_write=False,
        query_length=len(cleaned),
    )
    if not result.hits:
        return ChatAskResponse(
            answer=NO_MATCH_ANSWER,
            citations=[],
            retrieval_count=0,
            mailbox=mailbox,
            refused_write=False,
        )
    return ChatAskResponse(
        answer=sanitize_chat_answer(result.answer) if result.answer else NO_MATCH_ANSWER,
        citations=citations_from_hits(result.hits),
        retrieval_count=len(result.hits),
        mailbox=mailbox,
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
    history: list[ChatHistoryTurn] | None = None,
) -> AsyncIterator[dict]:
    """SSE payload dicts: status, meta, zero or more deltas, then done."""
    cleaned = sanitize_user_text(message)
    refused_write = detect_write_intent(cleaned)
    retrieval_limit = CHAT_DEFAULT_LIMIT if limit is None else limit
    prior = _cleaned_history(history)

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
        yield {"type": "done"}
        return

    async def execute(name: str, arguments: dict):
        return await execute_chat_tool(
            session,
            settings,
            openai_client=openai_client,
            name=name,
            arguments=arguments,
            mailbox=mailbox,
            limit=retrieval_limit,
        )

    answer = ""
    hits: list[SearchHit] = []
    forwarded_delta = False
    meta_sent = False
    async for event in iter_chat_agent(
        client=anthropic_client,
        settings=settings,
        question=cleaned,
        execute_tool=execute,
        history=prior,
    ):
        if event.get("type") == "status":
            yield {"type": "status", "text": event.get("text") or ""}
        elif event.get("type") == "retrieved":
            hits = list(event.get("hits") or [])
            if not meta_sent:
                yield _meta_event(
                    citations=citations_from_hits(hits),
                    retrieval_count=len(hits),
                    mailbox=mailbox,
                    refused_write=False,
                )
                meta_sent = True
        elif event.get("type") == "delta":
            piece = str(event.get("text") or "")
            if not piece:
                continue
            if not meta_sent:
                yield _meta_event(
                    citations=citations_from_hits(hits),
                    retrieval_count=len(hits),
                    mailbox=mailbox,
                    refused_write=False,
                )
                meta_sent = True
            forwarded_delta = True
            yield {"type": "delta", "text": piece}
        elif event.get("type") == "result":
            answer = str(event.get("answer") or "")
            hits = list(event.get("hits") or [])

    if not hits:
        citations: list[ChatCitation] = []
        text = NO_MATCH_ANSWER
        count = 0
    else:
        citations = citations_from_hits(hits)
        text = sanitize_chat_answer(answer) if answer else NO_MATCH_ANSWER
        count = len(hits)
    if not meta_sent:
        yield _meta_event(
            citations=citations,
            retrieval_count=count,
            mailbox=mailbox,
            refused_write=False,
        )
    if not forwarded_delta:
        yield {"type": "delta", "text": text}
    yield {"type": "done"}
