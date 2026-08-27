"""Backfill Graph meetingMessageType on empty-body messages.

Also drops erroneous ``sent_replies`` rows that resolved a thread from a
meeting accept (empty snapshot linked to a Graph meeting message), and
optionally recomputes ``body_clean`` for rows below CLEAN_VERSION.

Usage (from backend/):
  .venv/bin/python -m scripts.backfill_meeting_messages          # dry run
  .venv/bin/python -m scripts.backfill_meeting_messages --apply
  .venv/bin/python -m scripts.backfill_meeting_messages --apply --recompute-clean
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, or_, select, update

from app.core.dependencies import close_redis, get_graph_auth, get_graph_client, get_redis
from app.core.exceptions import GraphClientError
from app.db.session import dispose_engine, get_session_factory
from app.llm import email_clean
from app.models.db.message import Message
from app.models.db.sent_reply import SentReply
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum

logger = structlog.get_logger(__name__)


async def _backfill_meeting_types(*, graph, apply: bool) -> tuple[int, int]:
    """Return (examined, updated) counts.

    Candidates are rows still missing ``meeting_message_type``. Empty body was
    too narrow — Outlook accepts often have a non-empty preview/subject like
    ``Accepted: …`` while Graph still exposes ``meetingMessageType``.
    """
    factory = get_session_factory()
    examined = 0
    updated = 0

    async with factory() as session:
        stmt = (
            select(Message, Thread)
            .join(Thread, Message.thread_id == Thread.id)
            .where(Message.meeting_message_type.is_(None))
            .where(
                or_(
                    Message.body_text == "",
                    Message.body_text.is_(None),
                    Message.body_preview.is_(None),
                    Message.body_preview == "",
                    Thread.subject.ilike("Accepted:%"),
                    Thread.subject.ilike("Declined:%"),
                    Thread.subject.ilike("Tentative:%"),
                    Thread.subject.ilike("Canceled:%"),
                    Thread.subject.ilike("Cancelled:%"),
                    Message.body_preview.ilike("Accepted:%"),
                    Message.body_preview.ilike("Declined:%"),
                    Message.body_preview.ilike("Tentative:%"),
                )
            )
            .order_by(Message.received_at.desc())
            .limit(500)
        )
        rows = list((await session.execute(stmt)).all())

    for message, thread in rows:
        examined += 1
        try:
            graph_msg = await graph.get_message(thread.mailbox, message.graph_message_id)
        except GraphClientError as exc:
            logger.warning(
                "meeting_backfill_graph_failed",
                message_id=str(message.id),
                error=str(exc),
            )
            continue

        meeting_type = graph_msg.meeting_message_type
        response_type = graph_msg.response_type
        if not meeting_type:
            logger.info(
                "meeting_backfill_not_meeting",
                message_id=str(message.id),
                subject=thread.subject,
            )
            continue

        logger.info(
            "meeting_backfill_hit",
            message_id=str(message.id),
            meeting_message_type=meeting_type,
            response_type=response_type,
            apply=apply,
        )
        if not apply:
            updated += 1
            continue

        async with factory() as session:
            await session.execute(
                update(Message)
                .where(Message.id == message.id)
                .values(
                    meeting_message_type=meeting_type,
                    meeting_response_type=response_type,
                )
            )
            # Drop empty sent_reply that incorrectly resolved from this meeting.
            sr = (
                await session.execute(select(SentReply).where(SentReply.message_id == message.id))
            ).scalar_one_or_none()
            if sr is not None and not (sr.sent_body_snapshot or "").strip():
                thread_id = sr.thread_id
                await session.execute(delete(SentReply).where(SentReply.id == sr.id))
                other = (
                    await session.execute(
                        select(SentReply.id).where(SentReply.thread_id == thread_id).limit(1)
                    )
                ).scalar_one_or_none()
                if other is None:
                    await session.execute(
                        update(Thread)
                        .where(
                            Thread.id == thread_id,
                            Thread.state == ThreadStateEnum.RESOLVED.value,
                        )
                        .values(state=ThreadStateEnum.DRAFTED.value)
                    )
            await session.commit()
        updated += 1

    return examined, updated


async def _recompute_body_clean(*, apply: bool) -> tuple[int, int]:
    factory = get_session_factory()
    examined = 0
    updated = 0
    async with factory() as session:
        stmt = (
            select(Message)
            .where(
                or_(
                    Message.body_clean_version.is_(None),
                    Message.body_clean_version < email_clean.CLEAN_VERSION,
                )
            )
            .order_by(Message.received_at.desc())
            .limit(500)
        )
        messages = list((await session.execute(stmt)).scalars().all())
        for message in messages:
            examined += 1
            cleaned = email_clean.clean_email_body(
                message.body_text or "",
                content_type=message.body_content_type or "text",
            )
            if not apply:
                updated += 1
                continue
            message.body_clean = cleaned.body_clean
            message.body_clean_version = email_clean.CLEAN_VERSION
            message.body_clean_computed_at = datetime.now(UTC)
            updated += 1
        if apply:
            await session.commit()
    return examined, updated


async def _run(*, apply: bool, recompute_clean: bool) -> int:
    from app.core.config import get_settings

    settings = get_settings()
    redis = await get_redis()
    auth = await get_graph_auth(settings, redis)
    graph = get_graph_client(auth)
    examined, updated = await _backfill_meeting_types(graph=graph, apply=apply)
    print(f"meeting types: examined={examined} updated={updated} apply={apply}")
    if recompute_clean:
        c_ex, c_up = await _recompute_body_clean(apply=apply)
        print(f"body_clean: examined={c_ex} updated={c_up} apply={apply}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default dry-run)")
    parser.add_argument(
        "--recompute-clean",
        action="store_true",
        help="Also recompute body_clean for stale CLEAN_VERSION rows",
    )
    args = parser.parse_args(argv)

    async def _main() -> int:
        try:
            return await _run(apply=args.apply, recompute_clean=args.recompute_clean)
        finally:
            await close_redis()
            await dispose_engine()

    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
