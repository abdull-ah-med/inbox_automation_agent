"""Mailbox thread listing for the web UI.

Authorization model: any authenticated active user may read all configured
``TARGET_MAILBOXES``. Per-user mailbox RBAC is intentionally out of scope for
this shared ops console.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.mailbox_keys import resolve_mailbox_email
from app.models.schemas.dashboard import ThreadList
from app.repositories import thread_repo


async def list_threads(
    session: AsyncSession,
    settings: Settings,
    *,
    mailbox_key: str,
    state: str | None = None,
    urgency: str | None = None,
    stale_only: bool = False,
    include_filtered: bool = False,
    cursor: str | None = None,
    limit: int = 25,
) -> ThreadList:
    email = resolve_mailbox_email(mailbox_key, settings.mailbox_list)
    if email is None or not settings.mailbox_allowed(email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found")

    items, next_cursor = await thread_repo.list_by_mailbox(
        session,
        email,
        state=state,
        urgency=urgency,
        stale_only=stale_only,
        stale_after_hours=settings.staleness_threshold_hours,
        include_filtered=include_filtered,
        cursor=cursor,
        limit=min(max(limit, 1), 100),
    )
    return ThreadList(items=items, next_cursor=next_cursor)
