"""Spam allowlist repository — reviewer-corrected senders, per mailbox."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_mail import extract_email_address
from app.models.db.spam_allowlist import SpamAllowlist


async def upsert(
    session: AsyncSession,
    *,
    mailbox: str,
    sender_address: str,
    thread_id: uuid.UUID | None,
    actor: str | None,
    note: str | None = None,
) -> str:
    """Insert the sender for this mailbox; keep the first correction if it exists."""
    address = extract_email_address(sender_address)
    if address is None:
        raise ValueError("sender_address must contain an email address")
    mailbox_key = mailbox.strip().lower()
    stmt = (
        insert(SpamAllowlist)
        .values(
            mailbox=mailbox_key,
            sender_address=address,
            thread_id=thread_id,
            actor=actor,
            note=note,
        )
        .on_conflict_do_nothing(constraint="uq_spam_allowlist_mailbox_sender")
    )
    await session.execute(stmt)
    await session.flush()
    return address


async def addresses_for_mailbox(session: AsyncSession, mailbox: str) -> frozenset[str]:
    mailbox_key = mailbox.strip().lower()
    stmt = select(SpamAllowlist.sender_address).where(SpamAllowlist.mailbox == mailbox_key)
    result = await session.execute(stmt)
    return frozenset(row[0] for row in result.all())
