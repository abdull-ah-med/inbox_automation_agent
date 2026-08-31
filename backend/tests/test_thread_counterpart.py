"""Thread last_sender is the other party, not our mailbox after we reply.

Worked examples from live mailboxes:
- notarysdca@gmail.com inbound, then info@ replies → From is gmail, not Internal
- outbound-only to nealdavien@yahoo.com → From is yahoo, not the mailbox
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.repositories import thread_repo

pytestmark = pytest.mark.db

MAILBOX = "info@sample-services.example.com"
T_OUT = datetime(2026, 8, 19, 16, 26, 45, tzinfo=UTC)


def _thread(*, conversation_id: str, subject: str, last_message_at: datetime) -> Thread:
    return Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id=conversation_id,
        subject=subject,
        state=ThreadStateEnum.DRAFTED.value,
        last_message_at=last_message_at,
    )


def _message(
    thread: Thread,
    *,
    direction: str,
    sender: str,
    to_recipients: list[str],
    received_at: datetime,
) -> Message:
    return Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction=direction,
        sender=sender,
        body_text="body",
        received_at=received_at,
        to_recipients=to_recipients,
        cc_recipients=[],
    )


@pytest.mark.asyncio
async def test_outbound_only_summary_from_is_yahoo_not_mailbox(db_session) -> None:
    thread = _thread(
        conversation_id="yahoo-outbound",
        subject="Re: Your Background Check Dispute Request",
        last_message_at=T_OUT,
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        _message(
            thread,
            direction="outbound",
            sender=MAILBOX,
            to_recipients=["nealdavien@yahoo.com"],
            received_at=T_OUT,
        )
    )
    await db_session.commit()

    summary = await thread_repo.build_thread_summary(
        db_session, thread_repo.ThreadSchema.model_validate(thread)
    )
    assert summary.last_sender == "nealdavien@yahoo.com"
    assert summary.triage is None or summary.triage.is_internal is not True


@pytest.mark.asyncio
async def test_gmail_thread_is_not_internal_after_mailbox_reply(db_session) -> None:
    thread = _thread(
        conversation_id="gmail-notary",
        subject="Re:",
        last_message_at=datetime(2026, 8, 18, 21, 22, tzinfo=UTC),
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add_all(
        [
            _message(
                thread,
                direction="inbound",
                sender="notarysdca@gmail.com",
                to_recipients=[MAILBOX],
                received_at=datetime(2026, 8, 18, 20, 38, tzinfo=UTC),
            ),
            _message(
                thread,
                direction="outbound",
                sender=MAILBOX,
                to_recipients=["notarysdca@gmail.com"],
                received_at=datetime(2026, 8, 18, 21, 22, tzinfo=UTC),
            ),
        ]
    )
    await db_session.commit()

    summary = await thread_repo.build_thread_summary(
        db_session, thread_repo.ThreadSchema.model_validate(thread)
    )
    assert summary.last_sender == "notarysdca@gmail.com"
    assert summary.triage is None or summary.triage.is_internal is not True
