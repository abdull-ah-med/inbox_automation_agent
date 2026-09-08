"""Shared SQL helpers for Settings memory list enrichment."""

from __future__ import annotations

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.sql import Subquery

from app.models.db.message import Message


def normalize_mailbox_allowlist(mailboxes: list[str]) -> list[str]:
    """Lowercase trimmed mailbox emails for case-insensitive SQL filters."""
    return [item.strip().lower() for item in mailboxes if item and item.strip()]


def mailbox_matches(column: ColumnElement[str], mailbox: str) -> ColumnElement[bool]:
    """Case-insensitive equality against one mailbox address."""
    return func.lower(column) == mailbox.strip().lower()


def mailbox_in_allowlist(column: ColumnElement[str], mailboxes: list[str]) -> ColumnElement[bool]:
    """Case-insensitive membership in an allowlist."""
    allowed = normalize_mailbox_allowlist(mailboxes)
    return func.lower(column).in_(allowed)


def latest_inbound_sender_subquery() -> Subquery:
    """One row per thread: most recent inbound message sender."""
    ranked = (
        select(
            Message.thread_id.label("thread_id"),
            Message.sender.label("sender"),
            func.row_number()
            .over(partition_by=Message.thread_id, order_by=Message.received_at.desc())
            .label("rn"),
        ).where(Message.direction == "inbound")
    ).subquery()
    return (
        select(ranked.c.thread_id, ranked.c.sender)
        .where(ranked.c.rn == 1)
        .subquery("latest_inbound_sender")
    )
