"""Dashboard overview service — composes mailbox aggregates + attention queue."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.mailbox_keys import infer_mailbox_key, mailbox_label
from app.models.schemas.dashboard import DashboardOverview, MailboxOverview
from app.repositories import audit_repo, thread_repo


async def _build_mailbox_overviews(
    session: AsyncSession,
    settings: Settings,
) -> list[MailboxOverview]:
    emails = settings.mailbox_list
    aggregates = await thread_repo.aggregate_overview(
        session,
        emails,
        stale_after_hours=settings.staleness_threshold_hours,
    )
    by_email = {str(row["mailbox"]).lower(): row for row in aggregates}
    recent_by_email = await thread_repo.list_recent_for_mailboxes(
        session,
        emails,
        per_mailbox=3,
        stale_after_hours=settings.staleness_threshold_hours,
    )

    mailboxes: list[MailboxOverview] = []
    for email in emails:
        key = str(infer_mailbox_key(email))
        row = by_email.get(email.lower(), {})
        mailboxes.append(
            MailboxOverview(
                mailbox=key,
                email_address=email,
                label=mailbox_label(email, key, owners=settings.mailbox_owner_map),
                thread_count=int(row.get("thread_count", 0) or 0),
                unread_count=0,
                awaiting_action_count=int(row.get("awaiting_action_count", 0) or 0),
                filtered_count=int(row.get("filtered_count", 0) or 0),
                stale_count=int(row.get("stale_count", 0) or 0),
                open_fyi_count=int(row.get("open_fyi_count", 0) or 0),
                recently_resolved_draftassistant_count=int(row.get("recently_resolved_draftassistant_count", 0) or 0),
                urgency_breakdown=dict(row.get("urgency_breakdown") or {}),
                recent_threads=recent_by_email.get(email, []),
            )
        )
    return mailboxes


async def get_overview(
    session: AsyncSession,
    settings: Settings,
    *,
    needs_attention_sort: Literal["urgency", "recent"] = "urgency",
) -> DashboardOverview:
    mailboxes = await _build_mailbox_overviews(session, settings)
    emails = settings.mailbox_list
    needs_attention = await thread_repo.list_needs_attention(
        session,
        emails,
        limit=15,
        sort=needs_attention_sort,
    )
    open_fyi = await thread_repo.list_open_fyi(session, emails, limit=15)
    recently_resolved_by_draftassistant = await thread_repo.list_recently_resolved_by_draftassistant(
        session,
        emails,
        limit=15,
    )
    recent_activity = await audit_repo.list_recent(
        session,
        mailboxes=emails,
        limit=25,
    )
    return DashboardOverview(
        mailboxes=mailboxes,
        total_threads=sum(m.thread_count for m in mailboxes),
        total_awaiting=sum(m.awaiting_action_count for m in mailboxes),
        total_stale=sum(m.stale_count for m in mailboxes),
        needs_attention=needs_attention,
        open_fyi=open_fyi,
        recently_resolved_by_draftassistant=recently_resolved_by_draftassistant,
        recent_activity=recent_activity,
        updated_at=datetime.now(UTC),
    )


async def list_mailbox_overviews(
    session: AsyncSession,
    settings: Settings,
) -> list[MailboxOverview]:
    return await _build_mailbox_overviews(session, settings)
