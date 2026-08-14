"""Ops metrics service: date windows, mailbox scope, and formula assembly.

Window tests use literal calendar dates. Rate tests use independent literals
(0.4, 0.0, 1.0), not `approvals / (approvals + rejects)` recomputed in the
test. End-to-end formula checks live in ``test_ops_metrics_repo`` and
``test_get_metrics_on_worked_example``.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.core.exceptions import InvalidDateRangeError, UnknownMailboxError
from app.models.schemas.ops_report import MailboxVolume, QueueSnapshot
from app.services import ops_metrics_service
from app.services.ops_report_renderer import briefing_text
from tests.ops_metrics_fixtures import (
    EXPECTED_APPROVAL_RATE,
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
    EXPECTED_SALES_VOLUME,
    EXPECTED_SPAM,
    EXPECTED_STALE,
    EXPECTED_TOTAL_VOLUME,
    QUEUE_NOW,
    SALES,
    WINDOW_END,
    WINDOW_START,
    seed_worked_example,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "local",
        "target_mailboxes": "sales@example.com,cr@example.com",
        "staleness_threshold_hours": 24,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_rolling_days_spans_seven_ny_calendar_days() -> None:
    now = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)  # 12:00 EDT Aug 12
    start, end = ops_metrics_service.rolling_days(now=now)
    ny = ZoneInfo("America/New_York")
    assert start.astimezone(ny).date().isoformat() == "2026-08-06"
    assert start.astimezone(ny).hour == 0
    assert end.astimezone(ny).date().isoformat() == "2026-08-12"
    assert end.astimezone(ny).hour == 23


def test_rolling_days_uses_ny_date_not_utc_date() -> None:
    """03:00 UTC Aug 12 is still Aug 11 in New York."""
    now = datetime(2026, 8, 12, 3, 0, tzinfo=UTC)
    start, end = ops_metrics_service.rolling_days(now=now)
    ny = ZoneInfo("America/New_York")
    assert start.astimezone(ny).date().isoformat() == "2026-08-05"
    assert end.astimezone(ny).date().isoformat() == "2026-08-11"


def test_inclusive_calendar_range_naive_dates_are_ny_days() -> None:
    start, end = ops_metrics_service.inclusive_calendar_range(
        datetime(2026, 8, 3),
        datetime(2026, 8, 9),
    )
    ny = ZoneInfo("America/New_York")
    assert start == datetime(2026, 8, 3, tzinfo=ny).astimezone(UTC)
    assert end.astimezone(ny).date().isoformat() == "2026-08-09"
    assert end.astimezone(ny).hour == 23


def test_resolve_query_window_requires_both_bounds() -> None:
    with pytest.raises(InvalidDateRangeError, match="both from and to"):
        ops_metrics_service.resolve_query_window(datetime(2026, 8, 3), None)


def test_last_calendar_week_on_wednesday_is_prior_mon_sun() -> None:
    now = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)
    start, end = ops_metrics_service.last_calendar_week("America/New_York", now=now)
    ny = ZoneInfo("America/New_York")
    assert start.astimezone(ny).date().isoformat() == "2026-08-03"
    assert start.astimezone(ny).weekday() == 0
    assert end.astimezone(ny).date().isoformat() == "2026-08-09"
    assert end.astimezone(ny).weekday() == 6


def test_last_calendar_week_on_monday_morning_is_still_prior_week() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)  # Monday 08:00 EDT
    start, end = ops_metrics_service.last_calendar_week("America/New_York", now=now)
    ny = ZoneInfo("America/New_York")
    assert start.astimezone(ny).date().isoformat() == "2026-08-03"
    assert end.astimezone(ny).date().isoformat() == "2026-08-09"


def test_validate_window_rejects_inverted_range() -> None:
    start = datetime(2026, 8, 12, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    with pytest.raises(InvalidDateRangeError, match="on or before"):
        ops_metrics_service.validate_window(start, end)


def test_validate_window_rejects_span_over_93_days() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 4, 6, tzinfo=UTC)
    with pytest.raises(InvalidDateRangeError, match="93"):
        ops_metrics_service.validate_window(start, end)


def test_validate_window_accepts_93_days() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 4, 4, tzinfo=UTC)
    out = ops_metrics_service.validate_window(start, end)
    assert out == (start, end)


def test_validate_window_coerces_naive_to_utc() -> None:
    start, end = ops_metrics_service.validate_window(
        datetime(2026, 8, 1, 0, 0, 0),
        datetime(2026, 8, 7, 0, 0, 0),
    )
    assert start.tzinfo == UTC
    assert end.tzinfo == UTC


@contextmanager
def _repo_patches(**overrides: object):
    defaults: dict[str, object] = {
        "volume_by_mailbox": AsyncMock(return_value=[]),
        "count_spam_filtered": AsyncMock(return_value=0),
        "count_approvals_rejects": AsyncMock(return_value=(0, 0)),
        "top_reject_themes": AsyncMock(return_value=[]),
        "avg_resolve_hours": AsyncMock(return_value=(None, 0)),
        "count_drafts_generated": AsyncMock(return_value=0),
        "volume_by_category": AsyncMock(return_value=[]),
        "queue_snapshot": AsyncMock(return_value=(QueueSnapshot(), {})),
    }
    defaults.update(overrides)
    with ExitStack() as stack:
        for name, value in defaults.items():
            stack.enter_context(
                patch(f"app.services.ops_metrics_service.ops_metrics_repo.{name}", value)
            )
        yield


def test_scoped_mailboxes_filters_to_one() -> None:
    settings = _settings()
    assert ops_metrics_service.scoped_mailboxes(settings, "sales") == ["sales@example.com"]
    assert ops_metrics_service.scoped_mailboxes(settings, "sales@example.com") == [
        "sales@example.com"
    ]


def test_scoped_mailboxes_unknown_raises() -> None:
    with pytest.raises(UnknownMailboxError):
        ops_metrics_service.scoped_mailboxes(_settings(), "other@example.com")


@pytest.mark.asyncio
async def test_approval_rate_is_zero_when_nothing_was_decided() -> None:
    session = AsyncMock()
    with _repo_patches(count_approvals_rejects=AsyncMock(return_value=(0, 0))):
        metrics = await ops_metrics_service.get_metrics(
            session, _settings(), WINDOW_START, WINDOW_END
        )
    assert metrics.approvals == 0
    assert metrics.rejects == 0
    assert metrics.approval_rate == 0.0


@pytest.mark.asyncio
async def test_approval_rate_is_one_when_every_decision_is_approve() -> None:
    session = AsyncMock()
    with _repo_patches(count_approvals_rejects=AsyncMock(return_value=(4, 0))):
        metrics = await ops_metrics_service.get_metrics(
            session, _settings(), WINDOW_START, WINDOW_END
        )
    assert metrics.approval_rate == 1.0


@pytest.mark.asyncio
async def test_approval_rate_is_two_fifths_for_two_approves_three_rejects() -> None:
    session = AsyncMock()
    with _repo_patches(count_approvals_rejects=AsyncMock(return_value=(2, 3))):
        metrics = await ops_metrics_service.get_metrics(
            session, _settings(), WINDOW_START, WINDOW_END
        )
    assert metrics.approval_rate == 0.4


@pytest.mark.asyncio
async def test_mailbox_key_and_queue_merge_onto_volume_rows() -> None:
    volume = [
        MailboxVolume(mailbox=SALES, email=SALES, thread_volume=4),
        MailboxVolume(mailbox="cr@example.com", email="cr@example.com", thread_volume=1),
    ]
    session = AsyncMock()
    with _repo_patches(
        volume_by_mailbox=AsyncMock(return_value=volume),
        queue_snapshot=AsyncMock(
            return_value=(
                QueueSnapshot(awaiting_action=3, stale=1),
                {SALES: (2, 1), "cr@example.com": (1, 0)},
            )
        ),
    ):
        metrics = await ops_metrics_service.get_metrics(
            session, _settings(), WINDOW_START, WINDOW_END
        )
    assert metrics.volume_by_mailbox[0].mailbox == "sales"
    assert metrics.volume_by_mailbox[0].awaiting_action == 2
    assert metrics.volume_by_mailbox[0].stale == 1
    assert metrics.volume_by_mailbox[1].mailbox == "cr"
    assert metrics.volume_by_mailbox[1].awaiting_action == 1
    assert metrics.total_volume == 5


@pytest.mark.asyncio
@pytest.mark.db
async def test_get_metrics_on_worked_example(db_session) -> None:
    await seed_worked_example(db_session)
    with patch("app.repositories.ops_metrics_repo.datetime") as mocked_dt:
        mocked_dt.now.return_value = QUEUE_NOW
        metrics = await ops_metrics_service.get_metrics(
            db_session, _settings(), WINDOW_START, WINDOW_END
        )
    assert metrics.total_volume == EXPECTED_TOTAL_VOLUME
    assert metrics.volume_by_mailbox[0].thread_volume == EXPECTED_SALES_VOLUME
    assert metrics.volume_by_mailbox[1].thread_volume == EXPECTED_CR_VOLUME
    assert metrics.spam_filtered == EXPECTED_SPAM
    assert metrics.drafts_generated == EXPECTED_DRAFTS_GENERATED
    assert metrics.approvals == EXPECTED_APPROVALS
    assert metrics.rejects == EXPECTED_REJECTS
    assert metrics.approval_rate == EXPECTED_APPROVAL_RATE
    assert metrics.avg_resolve_hours == pytest.approx(EXPECTED_AVG_RESOLVE_HOURS)
    assert metrics.resolve_sample_count == EXPECTED_RESOLVE_SAMPLE
    assert metrics.queue.awaiting_action == EXPECTED_AWAITING
    assert metrics.queue.stale == EXPECTED_STALE
    assert metrics.queue.filtered == EXPECTED_FILTERED
    assert metrics.queue.urgency_critical == EXPECTED_CRITICAL
    assert metrics.queue.urgency_high == EXPECTED_HIGH
    assert metrics.queue.urgency_normal == EXPECTED_NORMAL
    assert {row.reason_code: row.count for row in metrics.top_reject_themes} == {
        "tone": 2,
        "other": 1,
    }


@pytest.mark.asyncio
@pytest.mark.db
async def test_get_metrics_sales_scope_excludes_cr(db_session) -> None:
    await seed_worked_example(db_session)
    with patch("app.repositories.ops_metrics_repo.datetime") as mocked_dt:
        mocked_dt.now.return_value = QUEUE_NOW
        metrics = await ops_metrics_service.get_metrics(
            db_session, _settings(), WINDOW_START, WINDOW_END, mailbox="sales"
        )
    assert metrics.total_volume == EXPECTED_SALES_VOLUME
    assert [row.email for row in metrics.volume_by_mailbox] == [SALES]
    assert metrics.spam_filtered == 2
    assert metrics.queue.awaiting_action == 2
    assert metrics.queue.stale == 1


@pytest.mark.asyncio
@pytest.mark.db
async def test_briefing_prints_worked_example_numbers(db_session) -> None:
    await seed_worked_example(db_session)
    with patch("app.repositories.ops_metrics_repo.datetime") as mocked_dt:
        mocked_dt.now.return_value = QUEUE_NOW
        metrics = await ops_metrics_service.get_metrics(
            db_session, _settings(), WINDOW_START, WINDOW_END
        )
    text = briefing_text(metrics)
    assert "5 inbound threads" in text
    assert "3 filtered as spam or no-action" in text
    assert "40.0% approved" in text
    assert "2 approved, 3 rejected" in text
    assert "15.0 hours" in text
    assert "3 awaiting action" in text
    assert "1 stale" in text
