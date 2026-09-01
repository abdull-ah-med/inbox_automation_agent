"""Healer: stuck NEW threads and open outbound tips. Hand-counted fixtures."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.audit_event import AuditEvent
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.models.schemas.graph import GraphMessageSchema
from app.repositories import message_repo, thread_repo
from app.workers.heal_threads_worker import HEAL_THREADS_MAX_PER_RUN, run_heal_threads

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
T1 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 1, 13, 0, tzinfo=UTC)


def _graph_msg(message_id: str, received: str, sender: str) -> GraphMessageSchema:
    return GraphMessageSchema.model_validate(
        {
            "id": message_id,
            "subject": "Need docs",
            "from": {"emailAddress": {"address": sender}},
            "receivedDateTime": received,
            "conversationId": "conv-heal",
        }
    )


@asynccontextmanager
async def _factory_for(session: AsyncSession):
    yield session


async def _insert_thread(
    session: AsyncSession,
    *,
    conversation_id: str,
    state: str = ThreadStateEnum.NEW.value,
    subject: str = "Need docs",
):
    return await thread_repo.upsert_thread(
        session,
        mailbox=MAILBOX,
        conversation_id=conversation_id,
        subject=subject,
        last_message_at=T2,
        state=state,
    )


async def _add_message(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    graph_message_id: str,
    direction: str,
    sender: str,
    received_at: datetime,
):
    return await message_repo.create_message(
        session,
        thread_id=thread_id,
        graph_message_id=graph_message_id,
        direction=direction,
        sender=sender,
        body_text="body",
        body_preview="body",
        received_at=received_at,
        to_recipients=["other@example.com"],
    )


def _sync_graph_client() -> MagicMock:
    client = MagicMock()
    client.list_thread_messages = AsyncMock(
        return_value=[
            _graph_msg("msg-in", "2026-09-01T12:00:00Z", "client@example.com"),
            _graph_msg("msg-out", "2026-09-01T13:00:00Z", "elise@example.com"),
        ]
    )
    return client


def _followup_graph_client() -> MagicMock:
    client = MagicMock()
    client.list_thread_messages = AsyncMock(
        return_value=[
            _graph_msg("msg-in", "2026-09-01T12:00:00Z", "client@example.com"),
            _graph_msg("msg-out", "2026-09-01T13:00:00Z", "elise@example.com"),
            _graph_msg("msg-followup", "2026-09-01T14:00:00Z", "client@example.com"),
        ]
    )
    return client


@pytest.mark.asyncio
async def test_heal_stuck_new_dry_run_leaves_state(db_session: AsyncSession) -> None:
    thread = await _insert_thread(db_session, conversation_id="heal-new-dry")
    await _add_message(
        db_session,
        thread.id,
        graph_message_id="msg-in",
        direction="inbound",
        sender="client@example.com",
        received_at=T1,
    )
    await db_session.commit()

    result = await run_heal_threads(
        apply=False,
        session_factory=lambda: _factory_for(db_session),
        redis=AsyncMock(),
        graph_client=_sync_graph_client(),
        max_per_run=HEAL_THREADS_MAX_PER_RUN,
    )

    db_session.expire_all()
    row = await db_session.get(Thread, thread.id)
    assert row is not None
    assert row.state == ThreadStateEnum.NEW.value
    assert result.stuck_new == 1
    assert result.healed == 0
    audits = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "heal.stuck_new")
        )
    ).scalar_one()
    assert audits == 0


@pytest.mark.asyncio
async def test_heal_stuck_new_apply_leaves_new(db_session: AsyncSession) -> None:
    """Apply re-runs post-ingest triage; fixture ends REQUIRES_HUMAN (fake pipeline)."""
    thread = await _insert_thread(db_session, conversation_id="heal-new-apply")
    await _add_message(
        db_session,
        thread.id,
        graph_message_id="msg-in",
        direction="inbound",
        sender="client@example.com",
        received_at=T1,
    )
    await db_session.commit()

    async def fake_triage(**kwargs: object) -> SimpleNamespace:
        ingest = kwargs["ingest_result"]
        tid = uuid.UUID(str(ingest.thread_id))
        await thread_repo.set_thread_outcome(
            db_session,
            tid,
            state=ThreadStateEnum.REQUIRES_HUMAN.value,
        )
        await db_session.commit()
        return SimpleNamespace(draft_status="PENDING", triage=SimpleNamespace(is_spam=False))

    with patch(
        "app.workers.heal_threads_worker.pipeline_service.run_post_ingest_triage",
        new=AsyncMock(side_effect=fake_triage),
    ):
        result = await run_heal_threads(
            apply=True,
            session_factory=lambda: _factory_for(db_session),
            redis=AsyncMock(),
            graph_client=_sync_graph_client(),
            max_per_run=HEAL_THREADS_MAX_PER_RUN,
        )

    db_session.expire_all()
    row = await db_session.get(Thread, thread.id)
    assert row is not None
    assert row.state == ThreadStateEnum.REQUIRES_HUMAN.value
    assert result.stuck_new == 1
    assert result.healed == 1
    events = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type == "heal.stuck_new",
                    AuditEvent.conversation_id == "heal-new-apply",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].payload["thread_id"] == str(thread.id)
    assert events[0].payload["mailbox"] == MAILBOX
    assert events[0].payload["check"] == "stuck_new"


@pytest.mark.asyncio
async def test_heal_outbound_tip_apply_resolves(db_session: AsyncSession) -> None:
    thread = await _insert_thread(
        db_session,
        conversation_id="heal-out",
        state=ThreadStateEnum.DRAFTED.value,
    )
    await _add_message(
        db_session,
        thread.id,
        graph_message_id="msg-in",
        direction="inbound",
        sender="client@example.com",
        received_at=T1,
    )
    await _add_message(
        db_session,
        thread.id,
        graph_message_id="msg-out",
        direction="outbound",
        sender="elise@example.com",
        received_at=T2,
    )
    await db_session.commit()

    with patch(
        "app.workers.heal_threads_worker.sent_reply_learning_service.run_catchup_after_outbound",
        new=AsyncMock(return_value=None),
    ):
        result = await run_heal_threads(
            apply=True,
            session_factory=lambda: _factory_for(db_session),
            redis=AsyncMock(),
            graph_client=_sync_graph_client(),
            max_per_run=HEAL_THREADS_MAX_PER_RUN,
        )

    db_session.expire_all()
    row = await db_session.get(Thread, thread.id)
    assert row is not None
    assert row.state == ThreadStateEnum.RESOLVED.value
    assert result.outbound_tip == 1
    assert result.healed == 1
    events = list(
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.event_type == "heal.outbound_tip")
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].payload["thread_id"] == str(thread.id)


@pytest.mark.asyncio
async def test_heal_skips_when_graph_has_newer_inbound(db_session: AsyncSession) -> None:
    thread = await _insert_thread(
        db_session,
        conversation_id="heal-followup",
        state=ThreadStateEnum.DRAFTED.value,
    )
    await _add_message(
        db_session,
        thread.id,
        graph_message_id="msg-in",
        direction="inbound",
        sender="client@example.com",
        received_at=T1,
    )
    await _add_message(
        db_session,
        thread.id,
        graph_message_id="msg-out",
        direction="outbound",
        sender="elise@example.com",
        received_at=T2,
    )
    await db_session.commit()

    result = await run_heal_threads(
        apply=True,
        session_factory=lambda: _factory_for(db_session),
        redis=AsyncMock(),
        graph_client=_followup_graph_client(),
        max_per_run=HEAL_THREADS_MAX_PER_RUN,
    )

    db_session.expire_all()
    row = await db_session.get(Thread, thread.id)
    assert row is not None
    assert row.state == ThreadStateEnum.DRAFTED.value
    assert result.outbound_tip == 0
    assert result.healed == 0


@pytest.mark.asyncio
async def test_heal_does_not_resolve_inbound_tip(db_session: AsyncSession) -> None:
    thread = await _insert_thread(
        db_session,
        conversation_id="heal-inbound",
        state=ThreadStateEnum.DRAFTED.value,
    )
    await _add_message(
        db_session,
        thread.id,
        graph_message_id="msg-in",
        direction="inbound",
        sender="client@example.com",
        received_at=T2,
    )
    await db_session.commit()

    result = await run_heal_threads(
        apply=True,
        session_factory=lambda: _factory_for(db_session),
        redis=AsyncMock(),
        graph_client=_sync_graph_client(),
        max_per_run=HEAL_THREADS_MAX_PER_RUN,
    )

    db_session.expire_all()
    row = await db_session.get(Thread, thread.id)
    assert row is not None
    assert row.state == ThreadStateEnum.DRAFTED.value
    assert result.healed == 0


@pytest.mark.asyncio
async def test_heal_caps_at_fifty_threads(db_session: AsyncSession) -> None:
    assert HEAL_THREADS_MAX_PER_RUN == 50
    ids: list[uuid.UUID] = []
    for i in range(51):
        thread = await _insert_thread(
            db_session,
            conversation_id=f"heal-cap-{i}",
            subject=f"Cap {i}",
        )
        await _add_message(
            db_session,
            thread.id,
            graph_message_id=f"msg-cap-{i}",
            direction="inbound",
            sender="client@example.com",
            received_at=T1,
        )
        ids.append(thread.id)
    await db_session.commit()

    async def fake_triage(**kwargs: object) -> SimpleNamespace:
        ingest = kwargs["ingest_result"]
        tid = uuid.UUID(str(ingest.thread_id))
        await thread_repo.set_thread_outcome(
            db_session,
            tid,
            state=ThreadStateEnum.REQUIRES_HUMAN.value,
        )
        await db_session.commit()
        return SimpleNamespace(draft_status="PENDING", triage=SimpleNamespace(is_spam=False))

    with patch(
        "app.workers.heal_threads_worker.pipeline_service.run_post_ingest_triage",
        new=AsyncMock(side_effect=fake_triage),
    ):
        result = await run_heal_threads(
            apply=True,
            session_factory=lambda: _factory_for(db_session),
            redis=AsyncMock(),
            graph_client=_sync_graph_client(),
            max_per_run=HEAL_THREADS_MAX_PER_RUN,
        )

    db_session.expire_all()
    still_new = (
        await db_session.execute(
            select(func.count())
            .select_from(Thread)
            .where(
                Thread.id.in_(ids),
                Thread.state == ThreadStateEnum.NEW.value,
            )
        )
    ).scalar_one()
    assert still_new == 1
    assert result.healed == 50
    assert result.stuck_new == 50
