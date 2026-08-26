"""EXPLAIN oracles for D7 supporting indexes. Plans must name the new indexes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.db.classification import Classification
from app.models.db.draft import Draft
from app.models.db.message import Message
from app.models.db.thread import Thread

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
BASE = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


async def _explain(session, sql: str, params: dict) -> str:
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = (await session.execute(text(f"EXPLAIN {sql}"), params)).all()
    return "\n".join(row[0] for row in rows)


@pytest.mark.asyncio
async def test_recent_approved_drafts_use_approved_at_index(db_session) -> None:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="idx-approved",
        subject="Invoice",
        state="DRAFTED",
        last_message_at=BASE,
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        Draft(
            thread_id=thread.id,
            message_id=f"graph-{uuid.uuid4()}",
            subject="Invoice",
            body="Thanks",
            recipients={},
            teaching_note="note",
            approved_at=BASE,
        )
    )
    await db_session.commit()

    plan = await _explain(
        db_session,
        "SELECT id FROM drafts WHERE approved_at IS NOT NULL "
        "ORDER BY approved_at DESC LIMIT 20",
        {},
    )
    assert "ix_drafts_approved_at" in plan


@pytest.mark.asyncio
async def test_latest_teaching_note_uses_thread_created_index(db_session) -> None:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="idx-teaching",
        subject="Packet",
        state="DRAFTED",
        last_message_at=BASE,
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        Draft(
            thread_id=thread.id,
            message_id=f"graph-{uuid.uuid4()}",
            subject="Packet",
            body="Thanks",
            recipients={},
            teaching_note="use this greeting",
            created_at=BASE + timedelta(hours=1),
        )
    )
    await db_session.commit()

    plan = await _explain(
        db_session,
        "SELECT teaching_note FROM drafts "
        "WHERE thread_id = :tid ORDER BY created_at DESC LIMIT 1",
        {"tid": thread.id},
    )
    assert "ix_drafts_thread_id_created_at" in plan


@pytest.mark.asyncio
async def test_latest_classification_uses_message_created_index(db_session) -> None:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="idx-class",
        subject="Screen",
        state="NEW",
        last_message_at=BASE,
    )
    db_session.add(thread)
    await db_session.flush()
    message = Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="vendor@example.com",
        body_text="Please screen",
        received_at=BASE,
        to_recipients=[],
        cc_recipients=[],
    )
    db_session.add(message)
    await db_session.flush()
    db_session.add(
        Classification(
            message_id=message.id,
            category="sales",
            intent="request",
            urgency="NORMAL",
            confidence=0.9,
            entities={},
            model_version="test",
            created_at=BASE,
        )
    )
    await db_session.commit()

    plan = await _explain(
        db_session,
        "SELECT id FROM classifications "
        "WHERE message_id = :mid ORDER BY created_at DESC LIMIT 1",
        {"mid": message.id},
    )
    assert "ix_classifications_message_id_created_at" in plan
