"""Reply memory API — list / exclude approved replies used as tone references."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import SettingsDep, get_db
from app.core.dependencies_auth import CurrentAdmin, CurrentUser
from app.core.exceptions import ReplyMemoryNotFoundError
from app.core.rate_limit import limiter
from app.models.schemas.reply_memory import (
    ReplyMemoryResponseSchema,
    ReplyMemoryUpdateSchema,
)
from app.services import reply_memory_service

router = APIRouter(prefix="/api/reply-memory", tags=["reply-memory"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=list[ReplyMemoryResponseSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def list_reply_memory(
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _user: CurrentUser,
    mailbox: Annotated[str | None, Query(max_length=320)] = None,
) -> list[ReplyMemoryResponseSchema]:
    _ = request, response
    rows = await reply_memory_service.list_memories(
        session,
        mailbox=mailbox,
        mailboxes=list(settings.mailbox_list),
        limit=100,
    )
    return [ReplyMemoryResponseSchema.model_validate(row) for row in rows]


@router.patch(
    "/{reply_id}",
    response_model=ReplyMemoryResponseSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def update_reply_memory(
    reply_id: uuid.UUID,
    body: ReplyMemoryUpdateSchema,
    request: Request,
    response: Response,
    session: DbSession,
    _admin: CurrentAdmin,
) -> ReplyMemoryResponseSchema:
    _ = request, response
    updated = await reply_memory_service.set_excluded(
        session,
        reply_id,
        is_excluded=body.is_excluded,
    )
    if updated is None:
        raise ReplyMemoryNotFoundError(f"Reply memory not found: {reply_id}")
    await session.commit()
    return ReplyMemoryResponseSchema.model_validate(updated)
