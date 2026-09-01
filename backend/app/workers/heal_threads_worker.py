"""Recurring healer: stuck NEW threads and open outbound tips.

Does not send, move, or flag mail (Mail.Read). Does not mutate Redis dedup —
``pipeline_ready_for_dedup`` already leaves ``REQUIRES_HUMAN`` / failed triage
open for poll retry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from apscheduler.triggers.cron import CronTrigger
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.exceptions import AuditError
from app.db.session import get_session_factory
from app.models.db.thread import Thread
from app.models.schemas.email import (
    EmailDirectionEnum,
    EmailMessageSchema,
    ThreadContextSchema,
    ThreadStateEnum,
)
from app.models.schemas.graph import IngestResultSchema
from app.repositories import message_repo
from app.services import (
    audit_service,
    pipeline_service,
    sent_reply_learning_service,
    sent_reply_service,
)

logger = structlog.get_logger(__name__)

HEAL_THREADS_MAX_PER_RUN = 50


def heal_threads_cron_trigger(settings: Settings) -> CronTrigger:
    """6:00 and 14:00 in ``heal_threads_timezone`` (default US Eastern)."""
    return CronTrigger(
        hour=settings.heal_threads_cron_hours,
        minute=settings.heal_threads_cron_minute,
        timezone=settings.heal_threads_timezone,
    )


_OPEN_STATES = frozenset(
    {
        ThreadStateEnum.NEW.value,
        ThreadStateEnum.DRAFTED.value,
        ThreadStateEnum.AWAITING_CLIENT.value,
        ThreadStateEnum.AWAITING_VENDOR.value,
        ThreadStateEnum.AWAITING_PARTNER.value,
        ThreadStateEnum.REQUIRES_HUMAN.value,
    }
)


@dataclass(frozen=True)
class HealThreadsResult:
    examined: int
    healed: int
    stuck_new: int
    outbound_tip: int


def _session_factory(
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> Any:
    return session_factory or get_session_factory()


async def _audit_heal(
    session: AsyncSession,
    *,
    event_type: str,
    thread: Thread,
    check: str,
) -> None:
    try:
        await audit_service.log_event(
            session,
            event_type=event_type,
            conversation_id=thread.conversation_id,
            mailbox=thread.mailbox,
            payload={
                "thread_id": str(thread.id),
                "mailbox": thread.mailbox,
                "check": check,
            },
            actor="system",
        )
    except AuditError:
        logger.warning(
            "heal_audit_failed",
            event_type=event_type,
            thread_id=str(thread.id),
        )


def _ingest_from_stored_thread(
    thread: Thread,
    messages: list[message_repo.MessageSchema],
) -> IngestResultSchema | None:
    if not messages:
        return None
    email_messages = [
        EmailMessageSchema(
            message_id=m.graph_message_id,
            conversation_id=thread.conversation_id,
            mailbox=thread.mailbox,
            sender=m.sender,
            subject=thread.subject,
            body_text=m.body_text,
            body_preview=m.body_preview,
            received_at=m.received_at,
            direction=EmailDirectionEnum(m.direction),
            to_recipients=list(m.to_recipients),
            cc_recipients=list(m.cc_recipients),
            has_attachments=bool(m.has_attachments),
        )
        for m in messages
    ]
    newest = max(email_messages, key=lambda m: m.received_at)
    return IngestResultSchema(
        message_id=newest.message_id,
        status="retry_triage",
        thread_id=str(thread.id),
        conversation_id=thread.conversation_id,
        thread_context=ThreadContextSchema(
            conversation_id=thread.conversation_id,
            mailbox=thread.mailbox,
            subject=thread.subject,
            messages=email_messages,
        ),
    )


async def _load_threads(
    session: AsyncSession,
    *,
    states: frozenset[str],
    limit: int,
) -> list[Thread]:
    if limit <= 0:
        return []
    stmt = (
        select(Thread)
        .where(Thread.state.in_(list(states)))
        .order_by(Thread.last_message_at.desc().nullslast())
        .limit(limit)
    )
    threads = list((await session.execute(stmt)).scalars().all())
    for thread in threads:
        session.expunge(thread)
    return threads


async def _heal_stuck_new(
    thread: Thread,
    *,
    apply: bool,
    redis: Redis,
    settings: Settings,
    factory: Any,
) -> bool:
    async with factory() as session:
        messages = await message_repo.list_by_thread(session, thread.id)
    ingest = _ingest_from_stored_thread(thread, messages)
    if ingest is None:
        return False
    if not apply:
        return True
    state = await pipeline_service.run_post_ingest_triage(
        redis=redis,
        settings=settings,
        ingest_result=ingest,
        post_slack=True,
    )
    if state is None:
        return False
    async with factory() as session:
        await _audit_heal(
            session,
            event_type="heal.stuck_new",
            thread=thread,
            check="stuck_new",
        )
        await session.commit()
    return True


async def _heal_outbound_tip(
    thread: Thread,
    *,
    apply: bool,
    redis: Redis,
    settings: Settings,
    factory: Any,
    graph_client: object | None,
) -> bool:
    if graph_client is None:
        return False
    async with factory() as session:
        rows = await message_repo.list_by_thread(session, thread.id)
        if not rows:
            return False
        tip = max(rows, key=lambda row: row.received_at)
        if tip.direction != EmailDirectionEnum.OUTBOUND.value:
            return False
        in_sync = await sent_reply_service.graph_outbound_tip_in_sync(
            session,
            graph_client,
            mailbox=thread.mailbox,
            conversation_id=thread.conversation_id,
            thread_id=thread.id,
            trigger_graph_message_id=tip.graph_message_id,
        )
        if not in_sync:
            return False
        if not apply:
            return True
        await sent_reply_service.resolve_thread_from_outbound(
            session,
            thread_id=thread.id,
            message=tip,
            conversation_id=thread.conversation_id,
            mailbox=thread.mailbox,
        )
        await _audit_heal(
            session,
            event_type="heal.outbound_tip",
            thread=thread,
            check="outbound_tip",
        )
        await session.commit()
    await sent_reply_learning_service.run_catchup_after_outbound(
        redis=redis,
        settings=settings,
        thread_id=thread.id,
        mailbox=thread.mailbox,
        conversation_id=thread.conversation_id,
        outbound_graph_message_id=tip.graph_message_id,
        session_factory=factory,
        graph_client=graph_client,
    )
    return True


async def run_heal_threads(
    *,
    apply: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    redis: Redis | None = None,
    settings: Settings | None = None,
    graph_client: object | None = None,
    max_per_run: int | None = None,
) -> HealThreadsResult:
    """Dry-run by default. ``apply=True`` writes via existing services only."""
    resolved_settings = settings or get_settings()
    cap = HEAL_THREADS_MAX_PER_RUN if max_per_run is None else max_per_run
    factory = _session_factory(session_factory)
    if redis is None:
        from app.core.dependencies import get_redis

        redis = await get_redis()

    examined = 0
    healed = 0
    stuck_new = 0
    outbound_tip = 0
    seen: set[uuid.UUID] = set()
    remaining = cap

    async with factory() as session:
        new_threads = await _load_threads(
            session,
            states=frozenset({ThreadStateEnum.NEW.value}),
            limit=remaining,
        )
    for thread in new_threads:
        examined += 1
        seen.add(thread.id)
        did = await _heal_stuck_new(
            thread,
            apply=apply,
            redis=redis,
            settings=resolved_settings,
            factory=factory,
        )
        if not did:
            continue
        stuck_new += 1
        remaining -= 1
        if apply:
            healed += 1
        if remaining <= 0:
            break

    if remaining > 0:
        async with factory() as session:
            open_threads = await _load_threads(
                session,
                states=_OPEN_STATES,
                limit=remaining,
            )
        for thread in open_threads:
            if thread.id in seen:
                continue
            examined += 1
            did = await _heal_outbound_tip(
                thread,
                apply=apply,
                redis=redis,
                settings=resolved_settings,
                factory=factory,
                graph_client=graph_client,
            )
            if not did:
                continue
            outbound_tip += 1
            remaining -= 1
            if apply:
                healed += 1
            if remaining <= 0:
                break

    logger.info(
        "heal_threads_finished",
        apply=apply,
        examined=examined,
        healed=healed,
        stuck_new=stuck_new,
        outbound_tip=outbound_tip,
    )
    return HealThreadsResult(
        examined=examined,
        healed=healed,
        stuck_new=stuck_new,
        outbound_tip=outbound_tip,
    )


async def run_scheduled_heal_threads() -> None:
    """Scheduler entry. No-op when ``heal_threads_enabled`` is false."""
    settings = get_settings()
    if not settings.heal_threads_enabled:
        return
    try:
        from app.core.dependencies import get_graph_auth, get_graph_client, get_redis

        redis = await get_redis()
        graph_client = None
        try:
            auth = await get_graph_auth(settings, redis)
            graph_client = get_graph_client(auth)
        except Exception:
            logger.warning("heal_threads_graph_unavailable", exc_info=True)
        result = await run_heal_threads(
            apply=True,
            redis=redis,
            settings=settings,
            graph_client=graph_client,
            max_per_run=settings.heal_threads_max_per_run,
        )
        logger.info(
            "heal_threads_job_complete",
            examined=result.examined,
            healed=result.healed,
            stuck_new=result.stuck_new,
            outbound_tip=result.outbound_tip,
        )
    except Exception:
        logger.exception("heal_threads_job_failed")
