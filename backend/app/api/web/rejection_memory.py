"""Rejection memory API — list / exclude rejected drafts used as negative constraints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.web.mailbox_access import require_allowed_mailbox
from app.core.dependencies import SettingsDep, get_db
from app.core.dependencies_auth import CurrentAdmin, CurrentUser
from app.core.exceptions import RejectionMemoryNotFoundError
from app.core.rate_limit import limiter
from app.models.schemas.rejection_memory import (
    RejectionMemoryResponseSchema,
    RejectionMemoryUpdateSchema,
)
from app.services import rejection_memory_service

router = APIRouter(prefix="/api/rejection-memory", tags=["rejection-memory"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=list[RejectionMemoryResponseSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def list_rejection_memory(
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _user: CurrentUser,
    mailbox: Annotated[str | None, Query(max_length=320)] = None,
) -> list[RejectionMemoryResponseSchema]:
    _ = request, response
    if mailbox is not None:
        require_allowed_mailbox(settings, mailbox)
    rows = await rejection_memory_service.list_memories(
        session,
        mailbox=mailbox,
        mailboxes=list(settings.mailbox_list),
        limit=100,
    )
    return [RejectionMemoryResponseSchema.model_validate(row) for row in rows]


@router.patch(
    "/{memory_id}",
    response_model=RejectionMemoryResponseSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def update_rejection_memory(
    memory_id: uuid.UUID,
    body: RejectionMemoryUpdateSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
) -> RejectionMemoryResponseSchema:
    _ = request, response
    existing = await rejection_memory_service.get_memory_mailbox(session, memory_id)
    if existing is None or not settings.mailbox_allowed(existing):
        raise RejectionMemoryNotFoundError(f"Rejection memory not found: {memory_id}")
    updated = await rejection_memory_service.set_excluded(
        session,
        memory_id,
        is_excluded=body.is_excluded,
    )
    if updated is None:
        raise RejectionMemoryNotFoundError(f"Rejection memory not found: {memory_id}")
    await session.commit()
    return RejectionMemoryResponseSchema.model_validate(updated)
