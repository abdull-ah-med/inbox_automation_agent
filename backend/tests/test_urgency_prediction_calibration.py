"""DB tests for calibration_worker.detect_distribution_drift.

Worked example — KS-test drift detection:

Fixture A (no drift): 10 predictions in recent window + 10 in baseline only,
  all with HIGH prob ≈ 0.7. KS statistic ≈ 0 → is_drift=False.

Fixture B (drift): 10 baseline with HIGH prob = 0.05 (low-urgency baseline),
  10 recent with HIGH prob = 0.95 (high-urgency spike). KS statistic ≈ 1.0 → True.

Oracle: is_drift boundary is KS > 0.20.
  - Identical distribution: KS = 0.0 < 0.20 → False.
  - Opposite extremes: KS ≈ 1.0 > 0.20 → True.
  Values are hand-derived from the KS definition, not from detect_distribution_drift.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.db.draft import Draft
from app.models.db.thread import Thread
from app.models.db.urgency_prediction import UrgencyPrediction
from app.workers.calibration_worker import detect_distribution_drift

pytestmark = pytest.mark.db

NOW = datetime.now(UTC)
_RECENT_DAY = NOW - timedelta(days=3)  # within 7-day recent window
_BASELINE_DAY = NOW - timedelta(days=20)  # beyond 7d, within 30d baseline
SENDER_DOMAIN = "vendor.com"


async def _make_thread_and_draft(session, mailbox: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a Thread + Draft pair and return (thread_id, draft_id)."""
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=mailbox,
        conversation_id=str(uuid.uuid4()),
        subject="calibration test",
        state="new",
    )
    session.add(thread)
    await session.flush()

    draft = Draft(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=str(uuid.uuid4()),
        subject="re: calibration test",
        body="draft body",
        recipients={},
        teaching_note="",
    )
    session.add(draft)
    await session.flush()
    return thread.id, draft.id


async def _insert_prediction_at(
    session,
    *,
    mailbox: str,
    probs: dict,
    when: datetime,
) -> uuid.UUID:
    """Insert one UrgencyPrediction with an explicit created_at timestamp."""
    thread_id, draft_id = await _make_thread_and_draft(session, mailbox)
    pred = UrgencyPrediction(
        id=uuid.uuid4(),
        draft_id=draft_id,
        thread_id=thread_id,
        mailbox=mailbox,
        sender_domain=SENDER_DOMAIN,
        predicted_urgency="HIGH" if probs.get("HIGH", 0) > 0.5 else "NORMAL",
        probs=probs,
        final_urgency="HIGH" if probs.get("HIGH", 0) > 0.5 else "NORMAL",
    )
    session.add(pred)
    await session.flush()
    # Override the server-generated created_at with the desired timestamp.
    await session.execute(
        text("UPDATE urgency_predictions SET created_at = :ts WHERE id = :id"),
        {"ts": when, "id": pred.id},
    )
    return pred.id


async def test_stable_distribution_no_drift(db_session) -> None:
    """Identical distributions in recent and baseline → KS ≈ 0, is_drift=False."""
    high_probs = {"CRITICAL": 0.0, "HIGH": 0.7, "NORMAL": 0.25, "LOW": 0.05}
    mailbox = "stable@example.com"

    # 10 in recent window (within 7d)
    for _ in range(10):
        await _insert_prediction_at(db_session, mailbox=mailbox, probs=high_probs, when=_RECENT_DAY)
    # 10 in baseline only (beyond 7d, within 30d)
    for _ in range(10):
        await _insert_prediction_at(
            db_session, mailbox=mailbox, probs=high_probs, when=_BASELINE_DAY
        )

    results = await detect_distribution_drift(db_session, mailbox=mailbox)

    # Oracle: all probs identical → KS = 0.0 → is_drift = False
    assert len(results) == 1
    r = results[0]
    assert r.mailbox == mailbox
    assert r.sender_domain == SENDER_DOMAIN
    assert r.is_drift is False
    assert r.ks_statistic < 0.20
    # Recent 7d is excluded from the 30d baseline (would be 20 if leaked).
    assert r.n_recent == 10
    assert r.n_baseline == 10


async def test_shifted_distribution_triggers_drift(db_session) -> None:
    """Baseline LOW (HIGH≈0.05) vs recent HIGH (HIGH≈0.95) → KS large → is_drift=True."""
    low_probs = {"CRITICAL": 0.0, "HIGH": 0.05, "NORMAL": 0.85, "LOW": 0.10}
    high_probs = {"CRITICAL": 0.0, "HIGH": 0.95, "NORMAL": 0.04, "LOW": 0.01}
    mailbox = "drifted@example.com"

    # 10 in baseline with low HIGH prob
    for _ in range(10):
        await _insert_prediction_at(
            db_session, mailbox=mailbox, probs=low_probs, when=_BASELINE_DAY
        )
    # 10 in recent window with high HIGH prob
    for _ in range(10):
        await _insert_prediction_at(db_session, mailbox=mailbox, probs=high_probs, when=_RECENT_DAY)

    results = await detect_distribution_drift(db_session, mailbox=mailbox)

    # Oracle: opposite extremes → KS ≈ 1.0 > 0.20 → is_drift=True
    assert len(results) == 1
    r = results[0]
    assert r.mailbox == mailbox
    assert r.sender_domain == SENDER_DOMAIN
    assert r.is_drift is True
    assert r.ks_statistic > 0.20
    # Opposite extremes, disjoint windows → KS = 1.0; n_baseline is the 10
    # older rows only (would be 20 and KS≈0.5 if recent leaked into baseline).
    assert r.n_recent == 10
    assert r.n_baseline == 10
    assert r.ks_statistic == pytest.approx(1.0)


async def test_cold_start_below_minimum_rows_skipped(db_session) -> None:
    """Recent window with fewer than 7 rows → cold-start, no result returned."""
    high_probs = {"CRITICAL": 0.0, "HIGH": 0.9, "NORMAL": 0.08, "LOW": 0.02}
    mailbox = "coldstart@example.com"

    # Only 5 rows in recent window — below cold-start threshold of 7
    for _ in range(5):
        await _insert_prediction_at(db_session, mailbox=mailbox, probs=high_probs, when=_RECENT_DAY)

    results = await detect_distribution_drift(db_session, mailbox=mailbox)

    # Oracle: 5 < 7 (cold-start minimum) → no result (bucket skipped)
    assert results == []


async def test_empty_mailbox_returns_no_results(db_session) -> None:
    """No predictions for mailbox → empty results list."""
    results = await detect_distribution_drift(db_session, mailbox="empty@example.com")

    # Oracle: no predictions → no results
    assert results == []


def test_ks_statistic_separated_samples_is_one() -> None:
    from app.workers.calibration_worker import _ks_statistic

    assert _ks_statistic([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == 1.0


def test_ks_statistic_identical_samples_is_zero() -> None:
    from app.workers.calibration_worker import _ks_statistic

    assert _ks_statistic([0.1, 0.2, 0.3], [0.1, 0.2, 0.3]) == 0.0
