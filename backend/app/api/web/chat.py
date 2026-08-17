"""Read-only chat ask API — natural language in, grounded answer + citations out."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import AnthropicClientDep, OpenAIClientDep, get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import limiter
from app.models.schemas.chat import ChatAskRequest, ChatAskResponse
from app.services import chat_service

router = APIRouter(prefix="/api/chat", tags=["chat"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/ask",
    response_model=ChatAskResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
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
    )
