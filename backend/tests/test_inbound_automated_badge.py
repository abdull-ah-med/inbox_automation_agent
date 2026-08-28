"""Thread Automated badge follows the latest inbound, not any historical OOO."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.repositories import message_repo, thread_repo

pytestmark = pytest.mark.db

MAILBOX = "sampleagent@sample-site.example.com"


@pytest.mark.asyncio
async def test_inbound_automated_follows_latest_inbound_not_historical_ooo(
    db_session,
) -> None:
    """Beau OOO on Aug 25 must not badge Ruth's Aug 28 follow-up as Automated."""
    thread = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-ruth-beau-integration",
        subject="Re: SampleLab integration",
        last_message_at=datetime(2026, 8, 28, 17, 16, tzinfo=UTC),
    )
    await message_repo.create_message(
        db_session,
        thread_id=thread.id,
        graph_message_id="AAMk-beau-ooo",
        direction="inbound",
        sender="beau.norris@sample-lab-vendor.example.com",
        sender_name="Norris, Beau P",
        is_automated=True,
        body_text="I am currently out of the office",
        body_preview="I am currently out of the office",
        received_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )
    await message_repo.create_message(
        db_session,
        thread_id=thread.id,
        graph_message_id="AAMk-ruth-followup",
        direction="inbound",
        sender="ruth.hooker@sample-lab-vendor.example.com",
        sender_name="Hooker, Ruth E",
        is_automated=False,
        body_text="Following up on the integration",
        body_preview="Following up on the integration",
        received_at=datetime(2026, 8, 28, 17, 16, tzinfo=UTC),
    )

    other = await thread_repo.upsert_thread(
        db_session,
        mailbox=MAILBOX,
        conversation_id="conv-only-ooo",
        subject="Automatic reply: Re: packet",
        last_message_at=datetime(2026, 8, 28, 18, 0, tzinfo=UTC),
    )
    await message_repo.create_message(
        db_session,
        thread_id=other.id,
        graph_message_id="AAMk-other-ooo",
        direction="inbound",
        sender="jane@client.example",
        is_automated=True,
        body_text="Out of office",
        body_preview="Out of office",
        received_at=datetime(2026, 8, 28, 18, 0, tzinfo=UTC),
    )
    await db_session.commit()

    flags = await message_repo.inbound_automated_by_threads(
        db_session, [thread.id, other.id]
    )
    assert flags[thread.id] is False
    assert flags[other.id] is True
