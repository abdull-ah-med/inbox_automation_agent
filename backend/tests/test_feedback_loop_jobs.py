"""Feedback Loops workers are registered only when their owning flags are on.

Oracle: canary sweeper / scope decay must not run while the feature tables
are gated off (review: workers were scheduled unconditionally).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.core.config import Settings
from app.main import _schedule_feedback_loop_jobs


def _settings(**flags: bool) -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes="sales@example.com",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        **flags,
    )


def _job_ids(scheduler: MagicMock) -> list[str]:
    return [call.kwargs["id"] for call in scheduler.add_job.call_args_list]


def test_flags_off_schedules_only_weekly_rollup() -> None:
    scheduler = MagicMock()
    _schedule_feedback_loop_jobs(
        scheduler,
        _settings(
            feedback_atoms_enabled=False,
            promotion_proposals_enabled=False,
            urgency_probs_enabled=False,
            urgency_rules_enabled=False,
        ),
    )
    assert _job_ids(scheduler) == ["feedback_weekly_rollup"]


def test_flags_on_schedule_all_feedback_loop_jobs() -> None:
    scheduler = MagicMock()
    _schedule_feedback_loop_jobs(
        scheduler,
        _settings(
            feedback_atoms_enabled=True,
            promotion_proposals_enabled=True,
            urgency_probs_enabled=True,
            urgency_rules_enabled=True,
        ),
    )
    assert _job_ids(scheduler) == [
        "scope_decay",
        "urgency_proposals",
        "urgency_calibration",
        "urgency_canary_sweeper",
        "feedback_weekly_rollup",
    ]
