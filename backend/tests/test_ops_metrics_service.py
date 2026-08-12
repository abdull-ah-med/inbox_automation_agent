"""Ops metrics service: formulas, windows, mailbox scoping."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.core.exceptions import InvalidDateRangeError, UnknownMailboxError
from app.models.schemas.ops_report import MailboxVolume, QueueSnapshot, RejectThemeCount
from app.services import ops_metrics_service


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "local",
        "target_mailboxes": "sales@example.com,cr@example.com",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_rolling_days_spans_seven_ny_calendar_days() -> None:
    now = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)  # 12:00 EDT
    start, end = ops_metrics_service.rolling_days(now=now)
    ny = ZoneInfo("America/New_York")
    assert start.astimezone(ny).date().isoformat() == "2026-08-06"
    assert start.astimezone(ny).hour == 0
    assert end.astimezone(ny).date().isoformat() == "2026-08-12"
    assert end.astimezone(ny).hour == 23


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
async def test_get_metrics_approval_rate_and_empty_resolve() -> None:
    settings = _settings()
    start = datetime(2026, 8, 3, tzinfo=UTC)
    end = datetime(2026, 8, 9, 23, 59, tzinfo=UTC)
    volume = [
        MailboxVolume(mailbox="sales@example.com", email="sales@example.com", thread_volume=4),
        MailboxVolume(mailbox="cr@example.com", email="cr@example.com", thread_volume=0),
    ]
    session = AsyncMock()
    with _repo_patches(
        volume_by_mailbox=AsyncMock(return_value=volume),
        count_spam_filtered=AsyncMock(return_value=3),
        count_approvals_rejects=AsyncMock(return_value=(16, 3)),
        top_reject_themes=AsyncMock(
            return_value=[RejectThemeCount(reason_code="tone", count=2)]
        ),
        avg_resolve_hours=AsyncMock(return_value=(None, 0)),
        count_drafts_generated=AsyncMock(return_value=18),
        queue_snapshot=AsyncMock(
            return_value=(
                QueueSnapshot(awaiting_action=2, stale=1),
                {"sales@example.com": (2, 1), "cr@example.com": (0, 0)},
            )
        ),
    ):
        metrics = await ops_metrics_service.get_metrics(session, settings, start, end)

    assert metrics.total_volume == 4
    assert metrics.spam_filtered == 3
    assert metrics.drafts_generated == 18
    assert metrics.approvals == 16
    assert metrics.rejects == 3
    assert metrics.approval_rate == pytest.approx(16 / 19)
    assert metrics.avg_resolve_hours is None
    assert metrics.resolve_sample_count == 0
    assert metrics.volume_by_mailbox[0].mailbox == "sales"
    assert metrics.volume_by_mailbox[0].awaiting_action == 2
    assert metrics.volume_by_mailbox[0].stale == 1
    assert metrics.queue.awaiting_action == 2
    assert metrics.top_reject_themes[0].reason_code == "tone"


@pytest.mark.asyncio
async def test_get_metrics_empty_week_zeros() -> None:
    settings = _settings()
    start = datetime(2026, 8, 3, tzinfo=UTC)
    end = datetime(2026, 8, 9, tzinfo=UTC)
    volume = [
        MailboxVolume(mailbox="sales@example.com", email="sales@example.com", thread_volume=0),
        MailboxVolume(mailbox="cr@example.com", email="cr@example.com", thread_volume=0),
    ]
    session = AsyncMock()
    with _repo_patches(volume_by_mailbox=AsyncMock(return_value=volume)):
        metrics = await ops_metrics_service.get_metrics(session, settings, start, end)

    assert metrics.total_volume == 0
    assert metrics.spam_filtered == 0
    assert metrics.approval_rate == 0.0
    assert metrics.avg_resolve_hours is None
    assert metrics.resolve_sample_count == 0
    assert metrics.top_reject_themes == []


@pytest.mark.asyncio
async def test_get_metrics_resolve_average() -> None:
    settings = _settings()
    start = datetime(2026, 8, 3, tzinfo=UTC)
    end = datetime(2026, 8, 9, tzinfo=UTC)
    session = AsyncMock()
    with _repo_patches(avg_resolve_hours=AsyncMock(return_value=(6.5, 4))):
        metrics = await ops_metrics_service.get_metrics(session, settings, start, end)

    assert metrics.avg_resolve_hours == pytest.approx(6.5)
    assert metrics.resolve_sample_count == 4
