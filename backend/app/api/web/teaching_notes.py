"""Teaching notes API — create, list, update, archive."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import SettingsDep, get_db
from app.core.dependencies_auth import CurrentAdmin, CurrentUser
from app.core.rate_limit import api_default_limit_value, limiter
from app.core.scope_keys import expires_at_for_scope, resolve_widening_scope, scope_key_for
from app.models.schemas.teaching_note import (
    CreateTeachingNoteSchema,
    TeachingNoteResponseSchema,
    UpdateTeachingNoteSchema,
    scope_is_wider,
)
from app.repositories import promotion_proposal_repo, teaching_note_repo

router = APIRouter(prefix="/api/teaching-notes", tags=["teaching-notes"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

_NOTE_NOT_FOUND_STATUS = status.HTTP_404_NOT_FOUND
_CREATE_SCOPES = frozenset(
    {
        "sender_address",
        "sender_domain",
        "mailbox+routing_category",
        "mailbox",
    }
)


def _require_allowed_mailbox(settings: SettingsDep, mailbox: str) -> None:
    if not settings.mailbox_allowed(mailbox):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mailbox not found",
        )


@router.get(
    "",
    response_model=list[TeachingNoteResponseSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit(api_default_limit_value)
async def list_teaching_notes(
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _user: CurrentUser,
    mailbox: Annotated[str, Query(min_length=1, max_length=320)],
    scope: Annotated[str | None, Query(max_length=40)] = None,
    note_status: Annotated[str | None, Query(alias="status", max_length=20)] = None,
) -> list[TeachingNoteResponseSchema]:
    _ = request, response
    _require_allowed_mailbox(settings, mailbox)
    rows = await teaching_note_repo.list_teaching_notes(
        session,
        mailbox=mailbox,
        scope=scope,
        status=note_status,
        limit=200,
    )
    return [TeachingNoteResponseSchema.model_validate(row) for row in rows]


@router.post(
    "",
    response_model=TeachingNoteResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("60/minute")
async def create_teaching_note(
    body: CreateTeachingNoteSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
) -> TeachingNoteResponseSchema:
    _ = request, response
    _require_allowed_mailbox(settings, body.mailbox)
    if body.scope == "global" or body.scope not in _CREATE_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Global scope is not permitted",
        )
    try:
        scope_key = scope_key_for(
            body.scope,
            sender_address=body.sender_address,
            sender_domain=body.sender_domain,
            mailbox=body.mailbox,
            routing_category=body.routing_category,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    note = await teaching_note_repo.create_teaching_note(
        session,
        mailbox=body.mailbox,
        title=body.title,
        body=body.body,
        scope=body.scope,
        scope_key=scope_key,
        applies_when=body.applies_when,
        origin="manual",
        person_bound=body.person_bound,
        created_by_user_id=_admin.id,
    )
    await session.commit()
    return TeachingNoteResponseSchema.model_validate(note)


@router.patch(
    "/{note_id}",
    response_model=TeachingNoteResponseSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def update_teaching_note(
    note_id: uuid.UUID,
    body: UpdateTeachingNoteSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
) -> TeachingNoteResponseSchema:
    """Update body/applies_when/title.

    If ``scope`` is provided and is wider than the current note scope,
    a ``note_widening`` promotion proposal is created instead of directly
    widening the scope. Global scope is never allowed (returns 400).
    Content fields from the same request are still applied.
    """
    _ = request, response
    existing = await teaching_note_repo.get_teaching_note_by_id(session, note_id)
    if existing is None:
        raise HTTPException(status_code=_NOTE_NOT_FOUND_STATUS, detail="Teaching note not found")
    _require_allowed_mailbox(settings, existing.mailbox)

    requested_scope = body.scope
    if requested_scope == "global":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Global scope is not permitted",
        )

    proposal_id: uuid.UUID | None = None
    if requested_scope is not None and requested_scope != existing.scope:
        resolved_scope, resolved_key = resolve_widening_scope(
            requested_scope,
            mailbox=existing.mailbox,
            source_scope_key=existing.scope_key,
            payload={
                "sender_address": body.sender_address,
                "sender_domain": body.sender_domain,
                "routing_category": body.routing_category,
            },
        )
        if scope_is_wider(requested_scope, existing.scope):
            expires = datetime.now(UTC) + timedelta(days=30)
            proposal = await promotion_proposal_repo.create_promotion_proposal(
                session,
                mailbox=existing.mailbox,
                kind="note_widening",
                payload={
                    "note_id": str(note_id),
                    "current_scope": existing.scope,
                    "requested_scope": resolved_scope,
                    "requested_scope_key": resolved_key,
                    "person_bound": existing.person_bound,
                },
                impact_num=0,
                impact_den=0,
                evidence_ids=[note_id],
                expires_at=expires,
            )
            proposal_id = proposal.id
        else:
            await teaching_note_repo.update_teaching_note_scope(
                session,
                note_id,
                scope=resolved_scope,
                scope_key=resolved_key,
                expires_at=expires_at_for_scope(resolved_scope),
            )

    updated = await teaching_note_repo.update_teaching_note(
        session,
        note_id,
        body=body.body,
        applies_when=body.applies_when,
        title=body.title,
    )
    if updated is None:
        raise HTTPException(status_code=_NOTE_NOT_FOUND_STATUS, detail="Teaching note not found")
    await session.commit()
    payload = TeachingNoteResponseSchema.model_validate(updated)
    if proposal_id is not None:
        response.status_code = status.HTTP_202_ACCEPTED
        payload = payload.model_copy(update={"proposal_id": proposal_id})
    return payload


@router.delete(
    "/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit("60/minute")
async def archive_teaching_note(
    note_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
) -> None:
    _ = request, response
    existing = await teaching_note_repo.get_teaching_note_by_id(session, note_id)
    if existing is None:
        raise HTTPException(status_code=_NOTE_NOT_FOUND_STATUS, detail="Teaching note not found")
    _require_allowed_mailbox(settings, existing.mailbox)
    result = await teaching_note_repo.update_teaching_note_status(
        session,
        note_id,
        status="archived",
    )
    if result is None:
        raise HTTPException(status_code=_NOTE_NOT_FOUND_STATUS, detail="Teaching note not found")
    await session.commit()
