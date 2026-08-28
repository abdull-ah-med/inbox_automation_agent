"""Shared SQL helpers for Settings memory list enrichment."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.sql import Subquery

from app.models.db.message import Message


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
