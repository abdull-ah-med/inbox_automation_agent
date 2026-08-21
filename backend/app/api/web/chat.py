"""Read-only chat ask API — natural language in, grounded answer + citations out."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import AnthropicClientDep, OpenAIClientDep, get_db
from app.core.dependencies_auth import CurrentUser
from app.core.exceptions import ChatError
from app.core.rate_limit import chat_limit_value, chat_rate_limit_key, limiter
from app.models.schemas.chat import (
    ChatAskRequest,
    ChatAskResponse,
    ChatCitedThread,
    ChatSessionCreateRequest,
    ChatSessionCreateResponse,
    ChatSessionMessage,
    ChatSessionResponse,
)
from app.services import chat_service, chat_session_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/session",
    response_model=ChatSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(chat_limit_value, key_func=chat_rate_limit_key)
async def create_session(
    body: ChatSessionCreateRequest,
    request: Request,
    response: Response,
    session: DbSession,
    _user: CurrentUser,
) -> ChatSessionCreateResponse:
    """Create a durable InboxAssistant conversation the client can resume."""
    _ = request, response
    row = await chat_session_service.create_session(
        session,
        user_id=_user.id,
        mailbox=body.mailbox,
    )
    return ChatSessionCreateResponse(session_id=row.id, mailbox=row.mailbox)


@router.get(
    "/session/{session_id}",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit(chat_limit_value, key_func=chat_rate_limit_key)
async def get_session(
    session_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    _user: CurrentUser,
) -> ChatSessionResponse:
    """Load a prior conversation owned by the current user."""
    _ = request, response
    row = await chat_session_service.get_session(
        session,
        session_id=session_id,
        user_id=_user.id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    messages: list[ChatSessionMessage] = []
    for item in row.messages or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        citations: list[ChatCitedThread] = []
        for cited in item.get("citations") or []:
            if not isinstance(cited, dict):
                continue
            try:
                citations.append(
                    ChatCitedThread(
                        thread_id=uuid.UUID(str(cited["thread_id"])),
                        subject=cited.get("subject"),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        messages.append(
            ChatSessionMessage(role=role, content=content, citations=citations)
        )
    return ChatSessionResponse(
        session_id=row.id,
        mailbox=row.mailbox,
        messages=messages,
        summary=row.summary or "",
    )


@router.post(
    "/ask",
    response_model=ChatAskResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit(chat_limit_value, key_func=chat_rate_limit_key)
async def ask(
    body: ChatAskRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    anthropic_client: AnthropicClientDep,
    _user: CurrentUser,
) -> ChatAskResponse:
    """Answer a natural-language question from hybrid search hits. Never mutates mail."""
    _ = request, response
    return await chat_service.ask(
        session,
        settings,
        openai_client=openai_client,
        anthropic_client=anthropic_client,
        message=body.message,
        mailbox=body.mailbox,
        limit=body.limit,
        history=body.history,
        user_key=str(_user.id),
        user_id=_user.id,
        bypass_cache=body.bypass_cache,
        session_id=body.session_id,
    )


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/ask/stream")
@limiter.limit(chat_limit_value, key_func=chat_rate_limit_key)
async def ask_stream(
    body: ChatAskRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    anthropic_client: AnthropicClientDep,
    _user: CurrentUser,
) -> StreamingResponse:
    """Same ask contract, streamed as SSE token deltas. Mail.Read only."""
    _ = request, response

    async def events() -> AsyncIterator[str]:
        try:
            async for payload in chat_service.iter_ask_events(
                session,
                settings,
                openai_client=openai_client,
                anthropic_client=anthropic_client,
                message=body.message,
                mailbox=body.mailbox,
                limit=body.limit,
                history=body.history,
                user_key=str(_user.id),
                user_id=_user.id,
                bypass_cache=body.bypass_cache,
                session_id=body.session_id,
            ):
                yield _sse_data(payload)
        except ChatError:
            yield _sse_data({"type": "error", "message": "Claude chat failed"})
        except Exception:
            logger.exception("chat_stream_failed")
            yield _sse_data({"type": "error", "message": "Claude chat failed"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
