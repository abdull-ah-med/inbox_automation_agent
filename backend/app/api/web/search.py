"""User-facing search over mailbox-scoped thread memory.

Default ``mode=keyword`` is FTS only. InboxAssistant uses ``hybrid`` via the chat API.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import OpenAIClientDep, get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import limiter
from app.models.schemas.search import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_MAX_LIMIT,
    SEARCH_MODE_KEYWORD,
    SEARCH_QUERY_MAX_CHARS,
    SearchResponse,
)
from app.services import search_service

router = APIRouter(prefix="/api", tags=["search"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.get(
    "/search",
    response_model=SearchResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def search(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    _user: CurrentUser,
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=SEARCH_QUERY_MAX_CHARS,
            description="Keyword text; matched with full-text search unless mode=hybrid",
        ),
    ],
    mailbox: Annotated[
        str | None,
        Query(description="Mailbox email or key; omit to search all allowed mailboxes"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=SEARCH_MAX_LIMIT, description="Max ranked thread hits"),
    ] = SEARCH_DEFAULT_LIMIT,
    mode: Annotated[
        Literal["keyword", "hybrid"],
        Query(description="keyword = FTS only (default). hybrid = vector + FTS"),
    ] = SEARCH_MODE_KEYWORD,
) -> SearchResponse:
    _ = request, response
    return await search_service.search_threads(
        session,
        settings,
        openai_client=openai_client,
        query=q,
        mailbox=mailbox,
        limit=limit,
        mode=mode,
    )
