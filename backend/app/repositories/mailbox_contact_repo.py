"""Mailbox contact repository — per-mailbox recipient aliases for salutations."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_mail import extract_email_address
from app.models.db.mailbox_contact import MailboxContact


@dataclass(frozen=True, slots=True)
class MailboxContactRow:
    id: uuid.UUID
    mailbox: str
    email: str
    full_name: str
    first_name: str
    notes: str | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


def _normalize_mailbox(mailbox: str) -> str:
    return mailbox.strip().lower()


def _normalize_email(email: str) -> str | None:
    extracted = extract_email_address(email)
    if extracted is not None:
        return extracted
    cleaned = email.strip().lower()
    return cleaned or None


def _to_row(model: MailboxContact) -> MailboxContactRow:
    return MailboxContactRow(
        id=model.id,
        mailbox=model.mailbox,
        email=model.email,
        full_name=model.full_name,
        first_name=model.first_name,
        notes=model.notes,
        created_by_user_id=model.created_by_user_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


async def get(
    session: AsyncSession,
    mailbox: str,
    email: str,
) -> MailboxContactRow | None:
    mailbox_key = _normalize_mailbox(mailbox)
    email_key = _normalize_email(email)
    if email_key is None:
        return None
    stmt = select(MailboxContact).where(
        MailboxContact.mailbox == mailbox_key,
        MailboxContact.email == email_key,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return _to_row(row) if row is not None else None


async def get_many(
    session: AsyncSession,
    mailbox: str,
    emails: Sequence[str],
) -> dict[str, MailboxContactRow]:
    """Bulk lookup keyed by normalized lowercase email. One query."""
    mailbox_key = _normalize_mailbox(mailbox)
    keys: list[str] = []
    seen: set[str] = set()
    for raw in emails:
        email_key = _normalize_email(raw)
        if email_key is None or email_key in seen:
            continue
        seen.add(email_key)
        keys.append(email_key)
    if not keys:
        return {}
    stmt = select(MailboxContact).where(
        MailboxContact.mailbox == mailbox_key,
        MailboxContact.email.in_(keys),
    )
    result = await session.execute(stmt)
    return {row.email: _to_row(row) for row in result.scalars().all()}


async def list_by_mailbox(
    session: AsyncSession,
    mailbox: str,
    *,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[MailboxContactRow], int]:
    mailbox_key = _normalize_mailbox(mailbox)
    filters = [MailboxContact.mailbox == mailbox_key]
    needle = (q or "").strip()
    if needle:
        pattern = f"%{needle}%"
        filters.append(
            or_(
                MailboxContact.email.ilike(pattern),
                MailboxContact.full_name.ilike(pattern),
                MailboxContact.first_name.ilike(pattern),
            )
        )
    count_stmt = select(func.count()).select_from(MailboxContact).where(*filters)
    total = int((await session.execute(count_stmt)).scalar_one())
    stmt = (
        select(MailboxContact)
        .where(*filters)
        .order_by(MailboxContact.email.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [_to_row(row) for row in result.scalars().all()], total


async def upsert(
    session: AsyncSession,
    mailbox: str,
    email: str,
    *,
    full_name: str,
    first_name: str,
    notes: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> tuple[MailboxContactRow, bool]:
    """Insert or update. Returns ``(row, created)``."""
    mailbox_key = _normalize_mailbox(mailbox)
    email_key = _normalize_email(email)
    if email_key is None:
        raise ValueError("email must contain an email address")
    first = first_name.strip()
    full = full_name.strip() or first
    if not first:
        raise ValueError("first_name is required")

    existing = await get(session, mailbox_key, email_key)
    created = existing is None
    stmt = (
        insert(MailboxContact)
        .values(
            id=uuid.uuid4(),
            mailbox=mailbox_key,
            email=email_key,
            full_name=full,
            first_name=first,
            notes=notes,
            created_by_user_id=actor_user_id,
        )
        .on_conflict_do_update(
            constraint="uq_mailbox_contacts_mailbox_email",
            set_={
                "full_name": full,
                "first_name": first,
                "notes": notes,
                "updated_at": func.now(),
            },
        )
        .returning(MailboxContact)
    )
    result = await session.execute(stmt)
    row = result.scalar_one()
    await session.flush()
    return _to_row(row), created


async def update(
    session: AsyncSession,
    mailbox: str,
    email: str,
    *,
    first_name: str | None = None,
    full_name: str | None = None,
    notes: str | None = None,
) -> MailboxContactRow | None:
    """Partial update. Returns None when the row does not exist (does not create)."""
    mailbox_key = _normalize_mailbox(mailbox)
    email_key = _normalize_email(email)
    if email_key is None:
        return None
    existing = await get(session, mailbox_key, email_key)
    if existing is None:
        return None
    values: dict[str, object] = {"updated_at": func.now()}
    if first_name is not None:
        cleaned = first_name.strip()
        if not cleaned:
            raise ValueError("first_name cannot be blank")
        values["first_name"] = cleaned
    if full_name is not None:
        values["full_name"] = full_name.strip()
    if notes is not None:
        values["notes"] = notes
    stmt = (
        sa_update(MailboxContact)
        .where(
            MailboxContact.mailbox == mailbox_key,
            MailboxContact.email == email_key,
        )
        .values(**values)
        .returning(MailboxContact)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    await session.flush()
    return _to_row(row) if row is not None else None


async def delete(session: AsyncSession, mailbox: str, email: str) -> bool:
    mailbox_key = _normalize_mailbox(mailbox)
    email_key = _normalize_email(email)
    if email_key is None:
        return False
    existing = await get(session, mailbox_key, email_key)
    if existing is None:
        return False
    stmt = sa_delete(MailboxContact).where(
        MailboxContact.mailbox == mailbox_key,
        MailboxContact.email == email_key,
    )
    result = await session.execute(stmt)
    await session.flush()
    return bool(result.rowcount)
