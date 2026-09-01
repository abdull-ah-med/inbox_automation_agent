"""Global contact greeting names for draft salutations.

Authorization: any authenticated active user may read/write contacts via any
configured TARGET_MAILBOXES path (same posture as the rest of the ops console —
per-user mailbox RBAC is out of scope). Contacts are shared across mailboxes;
the mailbox path segment only validates a known mailbox.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import limiter
from app.models.schemas.mailbox_contact import (
    MailboxContactListResponse,
    MailboxContactPatchSchema,
    MailboxContactUpsertSchema,
    MailboxContactView,
)
from app.services import mailbox_contact_service

router = APIRouter(prefix="/api/mailboxes", tags=["mailbox-contacts"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.get(
    "/{mailbox}/contacts",
    response_model=MailboxContactListResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def list_contacts(
    mailbox: str,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MailboxContactListResponse:
    _ = request, response
    async with session.begin():
        return await mailbox_contact_service.list_contacts(
            session,
            settings,
            mailbox_key=mailbox,
            q=q,
            limit=limit,
            offset=offset,
        )


@router.get(
    "/{mailbox}/contacts/by-email",
    response_model=MailboxContactView,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def get_contact(
    mailbox: str,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
    email: Annotated[str, Query(min_length=3, max_length=320)],
) -> MailboxContactView:
    _ = request, response
    async with session.begin():
        return await mailbox_contact_service.get_contact(
            session,
            settings,
            mailbox_key=mailbox,
            email=email,
        )


@router.post(
    "/{mailbox}/contacts",
    response_model=MailboxContactView,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("60/minute")
async def upsert_contact(
    mailbox: str,
    body: MailboxContactUpsertSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    user: CurrentUser,
) -> MailboxContactView:
    _ = request
    async with session.begin():
        view, created = await mailbox_contact_service.upsert_contact(
            session,
            settings,
            mailbox_key=mailbox,
            email=body.email,
            full_name=body.full_name,
            first_name=body.first_name,
            notes=body.notes,
            actor_user_id=user.id,
        )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return view


@router.patch(
    "/{mailbox}/contacts/by-email",
    response_model=MailboxContactView,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def patch_contact(
    mailbox: str,
    body: MailboxContactPatchSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
) -> MailboxContactView:
    _ = request, response
    async with session.begin():
        return await mailbox_contact_service.patch_contact(
            session,
            settings,
            mailbox_key=mailbox,
            email=body.email,
            first_name=body.first_name,
            full_name=body.full_name,
            notes=body.notes,
        )


@router.delete(
    "/{mailbox}/contacts/by-email",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit("60/minute")
async def delete_contact(
    mailbox: str,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
    email: Annotated[str, Query(min_length=3, max_length=320)],
) -> Response:
    _ = request
    async with session.begin():
        await mailbox_contact_service.delete_contact(
            session,
            settings,
            mailbox_key=mailbox,
            email=email,
        )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
