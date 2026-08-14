"""Ops metrics SQL: formula correctness against a worked-example week.

Expected numbers are hand-counted in ``ops_metrics_fixtures`` from the seeded
rows. These tests fail if volume counts messages, spam double-counts, windows
are exclusive, or mailboxes leak.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.email import EmailDirectionEnum
from app.repositories import ops_metrics_repo
from tests.ops_metrics_fixtures import (
    CR,
    EXPECTED_APPROVALS,
    EXPECTED_AVG_RESOLVE_HOURS,
    EXPECTED_AWAITING,
    EXPECTED_CR_VOLUME,
    EXPECTED_CRITICAL,
    EXPECTED_DRAFTS_GENERATED,
    EXPECTED_FILTERED,
    EXPECTED_HIGH,
    EXPECTED_NORMAL,
    EXPECTED_REJECTS,
    EXPECTED_RESOLVE_SAMPLE,
    EXPECTED_SALES_SPAM,
    EXPECTED_SALES_VOLUME,
    EXPECTED_SPAM,
    EXPECTED_STALE,
    EXPECTED_TOTAL_VOLUME,
    IN_WINDOW,
    MAILBOXES,
    QUEUE_NOW,
    SALES,
    STALE_AFTER_HOURS,
    WINDOW_END,
    WINDOW_START,
    seed_worked_example,
)

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_empty_mailbox_list_does_not_query() -> None:
    class _FailingSession:
        async def execute(self, stmt: object) -> object:
            raise AssertionError("empty mailbox list must not hit the database")

    session = _FailingSession()
    volume = await ops_metrics_repo.volume_by_mailbox(
        session, [], WINDOW_START, WINDOW_END  # type: ignore[arg-type]
    )
    spam = await ops_metrics_repo.count_spam_filtered(
        session, [], WINDOW_START, WINDOW_END  # type: ignore[arg-type]
    )
    approvals, rejects = await ops_metrics_repo.count_approvals_rejects(
        session, [], WINDOW_START, WINDOW_END  # type: ignore[arg-type]
    )
    themes = await ops_metrics_repo.top_reject_themes(
        session, [], WINDOW_START, WINDOW_END  # type: ignore[arg-type]
    )
    avg, n = await ops_metrics_repo.avg_resolve_hours(
        session, [], WINDOW_START, WINDOW_END  # type: ignore[arg-type]
    )
    drafts = await ops_metrics_repo.count_drafts_generated(
        session, [], WINDOW_START, WINDOW_END  # type: ignore[arg-type]
    )
    categories = await ops_metrics_repo.volume_by_category(
        session, [], WINDOW_START, WINDOW_END  # type: ignore[arg-type]
    )
    queue, by_mailbox = await ops_metrics_repo.queue_snapshot(session, [])  # type: ignore[arg-type]
    assert volume == []
    assert spam == 0
    assert (approvals, rejects) == (0, 0)
    assert themes == []
    assert avg is None and n == 0
    assert drafts == 0
    assert categories == []
    assert queue.awaiting_action == 0
    assert by_mailbox == {}


@pytest.mark.asyncio
async def test_volume_counts_threads_not_inbound_rows(db_session) -> None:
    await seed_worked_example(db_session)
    rows = await ops_metrics_repo.volume_by_mailbox(
        db_session, MAILBOXES, WINDOW_START, WINDOW_END
    )
    by_email = {row.email: row.thread_volume for row in rows}
    assert by_email[SALES] == EXPECTED_SALES_VOLUME
    assert by_email[CR] == EXPECTED_CR_VOLUME
    assert sum(by_email.values()) == EXPECTED_TOTAL_VOLUME
    inbound_in_window = await db_session.scalar(
        select(func.count(Message.id)).where(
            Message.direction == EmailDirectionEnum.INBOUND.value,
            Message.received_at >= WINDOW_START,
            Message.received_at <= WINDOW_END,
        )
    )
    assert inbound_in_window == 7
    assert inbound_in_window > EXPECTED_TOTAL_VOLUME


@pytest.mark.asyncio
async def test_volume_does_not_include_other_mailbox(db_session) -> None:
    await seed_worked_example(db_session)
    sales = await ops_metrics_repo.volume_by_mailbox(
        db_session, [SALES], WINDOW_START, WINDOW_END
    )
    cr = await ops_metrics_repo.volume_by_mailbox(
        db_session, [CR], WINDOW_START, WINDOW_END
    )
    assert sales[0].thread_volume == EXPECTED_SALES_VOLUME
    assert cr[0].thread_volume == EXPECTED_CR_VOLUME
    assert sales[0].email == SALES
    assert cr[0].email == CR


@pytest.mark.asyncio
async def test_inclusive_window_includes_start_and_end_not_outside(db_session) -> None:
    start_thread = Thread(
        mailbox=SALES, conversation_id="t-start", subject="start", state="NEW"
    )
    end_thread = Thread(
        mailbox=SALES, conversation_id="t-end", subject="end", state="NEW"
    )
    outside_thread = Thread(
        mailbox=SALES, conversation_id="t-outside", subject="outside", state="NEW"
    )
    db_session.add_all([start_thread, end_thread, outside_thread])
    await db_session.flush()
    db_session.add_all(
        [
            Message(
                thread_id=start_thread.id,
                graph_message_id="msg-start",
                direction=EmailDirectionEnum.INBOUND.value,
                sender="a@example.com",
                body_text="start",
                received_at=WINDOW_START,
                to_recipients=[],
                cc_recipients=[],
            ),
            Message(
                thread_id=end_thread.id,
                graph_message_id="msg-end",
                direction=EmailDirectionEnum.INBOUND.value,
                sender="a@example.com",
                body_text="end",
                received_at=WINDOW_END,
                to_recipients=[],
                cc_recipients=[],
            ),
            Message(
                thread_id=outside_thread.id,
                graph_message_id="msg-after",
                direction=EmailDirectionEnum.INBOUND.value,
                sender="a@example.com",
                body_text="after",
                received_at=WINDOW_END + timedelta(seconds=1),
                to_recipients=[],
                cc_recipients=[],
            ),
        ]
    )
    await db_session.commit()
    rows = await ops_metrics_repo.volume_by_mailbox(
        db_session, [SALES], WINDOW_START, WINDOW_END
    )
    assert rows[0].thread_volume == 2


@pytest.mark.asyncio
async def test_spam_dedupes_events_and_ignores_late(db_session) -> None:
    await seed_worked_example(db_session)
    both = await ops_metrics_repo.count_spam_filtered(
        db_session, MAILBOXES, WINDOW_START, WINDOW_END
    )
    sales = await ops_metrics_repo.count_spam_filtered(
        db_session, [SALES], WINDOW_START, WINDOW_END
    )
    assert both == EXPECTED_SPAM
    assert sales == EXPECTED_SALES_SPAM


@pytest.mark.asyncio
async def test_approvals_use_decision_time_not_created_at(db_session) -> None:
    await seed_worked_example(db_session)
    approvals, rejects = await ops_metrics_repo.count_approvals_rejects(
        db_session, MAILBOXES, WINDOW_START, WINDOW_END
    )
    assert approvals == EXPECTED_APPROVALS
    assert rejects == EXPECTED_REJECTS


@pytest.mark.asyncio
async def test_reject_themes_map_null_to_other_and_rank_by_count(db_session) -> None:
    await seed_worked_example(db_session)
    themes = await ops_metrics_repo.top_reject_themes(
        db_session, MAILBOXES, WINDOW_START, WINDOW_END, limit=5
    )
    by_code = {row.reason_code: row.count for row in themes}
    assert by_code == {"tone": 2, "other": 1}
    assert [row.reason_code for row in themes] == ["tone", "other"]


@pytest.mark.asyncio
async def test_resolve_average_excludes_negative_and_out_of_window(db_session) -> None:
    await seed_worked_example(db_session)
    avg, sample = await ops_metrics_repo.avg_resolve_hours(
        db_session, MAILBOXES, WINDOW_START, WINDOW_END
    )
    assert sample == EXPECTED_RESOLVE_SAMPLE
    assert avg == pytest.approx(EXPECTED_AVG_RESOLVE_HOURS)
    cr_avg, cr_n = await ops_metrics_repo.avg_resolve_hours(
        db_session, [CR], WINDOW_START, WINDOW_END
    )
    assert cr_n == 0
    assert cr_avg is None


@pytest.mark.asyncio
async def test_drafts_generated_uses_created_at_in_window(db_session) -> None:
    await seed_worked_example(db_session)
    assert (
        await ops_metrics_repo.count_drafts_generated(
            db_session, MAILBOXES, WINDOW_START, WINDOW_END
        )
        == EXPECTED_DRAFTS_GENERATED
    )


@pytest.mark.asyncio
async def test_category_groups_inbound_threads(db_session) -> None:
    await seed_worked_example(db_session)
    rows = await ops_metrics_repo.volume_by_category(
        db_session, MAILBOXES, WINDOW_START, WINDOW_END
    )
    by_cat = {row.category: row.count for row in rows}
    assert by_cat == {
        "billing": 2,
        "scheduling": 1,
        "client": 1,
        "uncategorized": 1,
    }
    assert sum(by_cat.values()) == EXPECTED_TOTAL_VOLUME


@pytest.mark.asyncio
async def test_queue_snapshot_is_current_state_not_period(db_session) -> None:
    await seed_worked_example(db_session)
    queue, by_mailbox = await ops_metrics_repo.queue_snapshot(
        db_session,
        MAILBOXES,
        stale_after_hours=STALE_AFTER_HOURS,
        now=QUEUE_NOW,
    )
    assert queue.awaiting_action == EXPECTED_AWAITING
    assert queue.stale == EXPECTED_STALE
    assert queue.filtered == EXPECTED_FILTERED
    assert queue.urgency_critical == EXPECTED_CRITICAL
    assert queue.urgency_high == EXPECTED_HIGH
    assert queue.urgency_normal == EXPECTED_NORMAL
    assert queue.urgency_low == 0
    assert by_mailbox[SALES] == (2, 1)
    assert by_mailbox[CR] == (1, 0)


@pytest.mark.asyncio
async def test_configured_mailbox_with_no_mail_still_listed(db_session) -> None:
    empty = "empty@example.com"
    thread = Thread(
        mailbox=SALES, conversation_id="only-sales", subject="x", state="NEW"
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        Message(
            thread_id=thread.id,
            graph_message_id="only-sales-msg",
            direction=EmailDirectionEnum.INBOUND.value,
            sender="a@example.com",
            body_text="x",
            received_at=IN_WINDOW,
            to_recipients=[],
            cc_recipients=[],
        )
    )
    await db_session.commit()
    rows = await ops_metrics_repo.volume_by_mailbox(
        db_session, [SALES, empty], WINDOW_START, WINDOW_END
    )
    assert [row.email for row in rows] == [SALES, empty]
    assert rows[0].thread_volume == 1
    assert rows[1].thread_volume == 0
