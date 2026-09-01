"""Mailbox contact service — global greeting-name CRUD (mailbox path is auth)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.mailbox_keys import resolve_mailbox_email
from app.models.schemas.mailbox_contact import (
    MailboxContactListResponse,
    MailboxContactView,
)
from app.repositories import mailbox_contact_repo
from app.repositories.mailbox_contact_repo import MailboxContactRow


def _resolve_mailbox(settings: Settings, mailbox_key: str) -> str:
    email = resolve_mailbox_email(mailbox_key, settings.mailbox_list)
    if email is None or not settings.mailbox_allowed(email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found")
    return email


def _to_view(row: MailboxContactRow) -> MailboxContactView:
    return MailboxContactView(
        email=row.email,
        full_name=row.full_name,
        first_name=row.first_name,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def list_contacts(
    session: AsyncSession,
    settings: Settings,
    *,
    mailbox_key: str,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> MailboxContactListResponse:
    # Mailbox path validates the caller may use the console; directory is global.
    _resolve_mailbox(settings, mailbox_key)
    rows, total = await mailbox_contact_repo.list_contacts(
        session,
        q=q,
        limit=limit,
        offset=offset,
    )
    return MailboxContactListResponse(items=[_to_view(r) for r in rows], total=total)


async def get_contact(
    session: AsyncSession,
    settings: Settings,
    *,
    mailbox_key: str,
    email: str,
) -> MailboxContactView:
    _resolve_mailbox(settings, mailbox_key)
    row = await mailbox_contact_repo.get(session, email)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return _to_view(row)


async def upsert_contact(
    session: AsyncSession,
    settings: Settings,
    *,
    mailbox_key: str,
    email: str,
    full_name: str,
    first_name: str,
    notes: str | None,
    actor_user_id: uuid.UUID | None,
) -> tuple[MailboxContactView, bool]:
    mailbox = _resolve_mailbox(settings, mailbox_key)
    try:
        row, created = await mailbox_contact_repo.upsert(
            session,
            mailbox,
            email,
            full_name=full_name,
            first_name=first_name,
            notes=notes,
            actor_user_id=actor_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_view(row), created


async def patch_contact(
    session: AsyncSession,
    settings: Settings,
    *,
    mailbox_key: str,
    email: str,
    first_name: str | None,
    full_name: str | None,
    notes: str | None,
) -> MailboxContactView:
    _resolve_mailbox(settings, mailbox_key)
    try:
        row = await mailbox_contact_repo.update(
            session,
            email,
            first_name=first_name,
            full_name=full_name,
            notes=notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return _to_view(row)


async def delete_contact(
    session: AsyncSession,
    settings: Settings,
    *,
    mailbox_key: str,
    email: str,
) -> None:
    _resolve_mailbox(settings, mailbox_key)
    deleted = await mailbox_contact_repo.delete(session, email)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
