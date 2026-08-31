"""Mailbox thread listing for the web UI.

Authorization model: any authenticated active user may read all configured
``TARGET_MAILBOXES``. Per-user mailbox RBAC is intentionally out of scope for
this shared ops console.
"""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.mailbox_keys import resolve_mailbox_email
from app.models.schemas.dashboard import ThreadList
from app.repositories import thread_repo


def _validate_date_range(date_from: date | None, date_to: date | None) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from must be on or before to",
        )


async def list_threads(
    session: AsyncSession,
    settings: Settings,
    *,
    mailbox_key: str,
    state: str | None = None,
    urgency: str | None = None,
    stale_only: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> ThreadList:
    email = resolve_mailbox_email(mailbox_key, settings.mailbox_list)
    if email is None or not settings.mailbox_allowed(email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found")

    _validate_date_range(date_from, date_to)

    items, next_cursor = await thread_repo.list_by_mailbox(
        session,
        email,
        state=state,
        urgency=urgency,
        stale_only=stale_only,
        stale_after_hours=settings.staleness_threshold_hours,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=min(max(limit, 1), 100),
    )
    return ThreadList(items=items, next_cursor=next_cursor)
