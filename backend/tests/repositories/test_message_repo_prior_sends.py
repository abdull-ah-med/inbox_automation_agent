"""Recent outbound-to-recipient query for draft grounding (Panza/Superhuman).

Worked example: mailbox sales@ sent two earlier letters to buyer@acme.com
on a different thread. The current thread's own outbound is excluded.
A send to other@example.com is excluded.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.db.message import Message
from app.models.db.thread import Thread
from app.repositories import message_repo

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
RECIPIENT = "buyer@acme.com"
PRIOR_BODY = "PRIOR-SEND-TO-ACME-PORTAL-FIX"
OTHER_BODY = "SEND-TO-SOMEONE-ELSE"
CURRENT_BODY = "CURRENT-THREAD-OUTBOUND"


async def _thread(session, subject: str) -> Thread:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id=str(uuid.uuid4()),
        subject=subject,
        state="RESOLVED",
    )
    session.add(thread)
    await session.flush()
    return thread


async def _outbound(
    session,
    thread: Thread,
    *,
    body: str,
    to: list[str],
    minute: int,
) -> Message:
    message = Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="outbound",
        sender=MAILBOX,
        body_text=body,
        body_clean=body,
        received_at=datetime(2026, 8, 1, 12, minute, tzinfo=UTC),
        to_recipients=to,
    )
    session.add(message)
    await session.flush()
    return message


async def test_list_recent_outbound_to_recipient_excludes_current_thread(db_session) -> None:
    prior_thread = await _thread(db_session, "old acme")
    other_thread = await _thread(db_session, "other client")
    current = await _thread(db_session, "current acme")
    await _outbound(db_session, prior_thread, body=PRIOR_BODY, to=[RECIPIENT], minute=1)
    await _outbound(db_session, other_thread, body=OTHER_BODY, to=["other@example.com"], minute=2)
    await _outbound(db_session, current, body=CURRENT_BODY, to=[RECIPIENT], minute=3)

    rows = await message_repo.list_recent_outbound_to_recipient(
        db_session,
        mailbox=MAILBOX,
        recipient=RECIPIENT,
        exclude_thread_id=current.id,
        limit=3,
    )
    assert [row.body_clean or row.body_text for row in rows] == [PRIOR_BODY]
