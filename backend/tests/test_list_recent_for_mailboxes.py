"""Dashboard card previews: top N awaiting threads per mailbox.

Oracle is a hand-counted fixture, not the SQL window. A busy mailbox that
sorts first must not starve a quieter mailbox of its card preview — that is
the EC2 home-page bug (counts said awaiting action, cards said none).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.repositories import thread_repo

pytestmark = pytest.mark.db

# aaa sorts before zzz; 30 > 2 mailboxes * 3 per card * 2 padding on the
# old global LIMIT, so a mailbox-ordered cap would return only aaa rows.
BUSY = "aaa@example.com"
QUIET = "zzz@example.com"
PER_MAILBOX = 3
BUSY_COUNT = 30
BASE = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _thread(
    *,
    mailbox: str,
    conversation_id: str,
    subject: str,
    state: str,
    last_message_at: datetime,
) -> Thread:
    return Thread(
        id=uuid.uuid4(),
        mailbox=mailbox,
        conversation_id=conversation_id,
        subject=subject,
        state=state,
        urgency="NORMAL",
        last_message_at=last_message_at,
    )


async def _seed_card_preview(session) -> None:
    rows = [
        _thread(
            mailbox=BUSY,
            conversation_id=f"alpha-{i:02d}",
            subject=f"alpha-{i:02d}",
            state=ThreadStateEnum.REQUIRES_HUMAN.value,
            last_message_at=BASE + timedelta(hours=i),
        )
        for i in range(BUSY_COUNT)
    ]
    rows.append(
        _thread(
            mailbox=QUIET,
            conversation_id="zeta-open",
            subject="zeta-open",
            state=ThreadStateEnum.REQUIRES_HUMAN.value,
            last_message_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        )
    )
    rows.append(
        _thread(
            mailbox=QUIET,
            conversation_id="zeta-done",
            subject="zeta-done",
            state=ThreadStateEnum.RESOLVED.value,
            last_message_at=datetime(2026, 8, 25, 18, 0, tzinfo=UTC),
        )
    )
    session.add_all(rows)
    await session.commit()


@pytest.mark.asyncio
async def test_quiet_mailbox_keeps_its_awaiting_preview_when_busy_mailbox_sorts_first(
    db_session,
) -> None:
    """30 REQUIRES_HUMAN on aaa@ + 1 open and 1 newer RESOLVED on zzz@.

    Card preview per mailbox is 3. zzz must still show zeta-open (the only
    open work), not an empty list and not the resolved sibling.
    aaa must show the three newest subjects: alpha-29, 28, 27.
    """
    await _seed_card_preview(db_session)

    buckets = await thread_repo.list_recent_for_mailboxes(
        db_session,
        [BUSY, QUIET],
        per_mailbox=PER_MAILBOX,
    )

    assert [row.subject for row in buckets[QUIET]] == ["zeta-open"]
    assert [row.subject for row in buckets[BUSY]] == [
        "alpha-29",
        "alpha-28",
        "alpha-27",
    ]
