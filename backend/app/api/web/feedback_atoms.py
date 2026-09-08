"""Feedback atoms API — list and exclude (deactivate) atoms."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import SettingsDep, get_db
from app.core.dependencies_auth import CurrentAdmin
from app.core.rate_limit import api_default_limit_value, limiter
from app.repositories import feedback_atom_repo
from app.repositories.feedback_atom_repo import FeedbackAtomSchema

router = APIRouter(prefix="/api/feedback-atoms", tags=["feedback-atoms"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=list[FeedbackAtomSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit(api_default_limit_value)
async def list_feedback_atoms(
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
    mailbox: Annotated[str, Query(min_length=1, max_length=320)],
    role: Annotated[str | None, Query(max_length=20)] = None,
    scope: Annotated[str | None, Query(max_length=40)] = None,
    active_only: Annotated[bool, Query()] = True,
) -> list[FeedbackAtomSchema]:
    _ = request, response
    if not settings.mailbox_allowed(mailbox):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mailbox not found",
        )
    return await feedback_atom_repo.list_atoms(
        session,
        mailbox=mailbox,
        role=role,
        scope=scope,
        is_active=True if active_only else None,
        limit=200,
    )


@router.post(
    "/{atom_id}/exclude",
    response_model=FeedbackAtomSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def exclude_feedback_atom(
    atom_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
) -> FeedbackAtomSchema:
    _ = request, response
    atom = await feedback_atom_repo.get_atom_by_id(session, atom_id)
    if atom is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback atom not found",
        )
    if not settings.mailbox_allowed(atom.mailbox):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mailbox not found",
        )
    atom = await feedback_atom_repo.deactivate_atom(session, atom_id)
    if atom is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback atom not found",
        )
    await session.commit()
    return atom
