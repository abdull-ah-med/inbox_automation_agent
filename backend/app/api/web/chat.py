"""Read-only chat ask API — natural language in, grounded answer + citations out."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import AnthropicClientDep, OpenAIClientDep, get_db
from app.core.dependencies_auth import CurrentUser
from app.core.exceptions import ChatError
from app.core.rate_limit import chat_limit_value, chat_rate_limit_key, limiter
from app.models.schemas.chat import ChatAskRequest, ChatAskResponse
from app.services import chat_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


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
        bypass_cache=body.bypass_cache,
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
                bypass_cache=body.bypass_cache,
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
