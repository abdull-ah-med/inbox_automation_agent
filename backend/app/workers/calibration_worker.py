"""Calibration worker — nightly KS-test for urgency prediction drift.

For each (mailbox, sender_domain) bucket compares the last 7-day urgency
probability distribution against a trailing 30-day baseline. Emits a
structlog warning when KS statistic > threshold.

Uses a linear two-pointer two-sample KS statistic on HIGH+CRITICAL probabilities.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.db.urgency_prediction import UrgencyPrediction

logger = structlog.get_logger(__name__)

_KS_DRIFT_THRESHOLD = 0.20
_COLD_START_MIN_ROWS = 7
_BASELINE_DAYS = 30
_RECENT_DAYS = 7
_PREDICTION_LOAD_CAP = 5000


def _ks_statistic(a: list[float], b: list[float]) -> float:
    """Two-sample KS statistic (D) via a linear two-pointer ECDF walk."""
    if not a or not b:
        return 0.0
    a_sorted = sorted(a)
    b_sorted = sorted(b)
    n_a = len(a_sorted)
    n_b = len(b_sorted)
    i = 0
    j = 0
    max_d = 0.0
    while i < n_a and j < n_b:
        if a_sorted[i] < b_sorted[j]:
            i += 1
        elif b_sorted[j] < a_sorted[i]:
            j += 1
        else:
            i += 1
            j += 1
        max_d = max(max_d, abs(i / n_a - j / n_b))
    while i < n_a:
        i += 1
        max_d = max(max_d, abs(i / n_a - j / n_b))
    while j < n_b:
        j += 1
        max_d = max(max_d, abs(i / n_a - j / n_b))
    return max_d


def _extract_high_prob(probs: dict) -> float:
    """Extract the HIGH+CRITICAL probability from a probs dict."""
    if not probs:
        return 0.0
    return float(probs.get("HIGH", 0.0)) + float(probs.get("CRITICAL", 0.0))


class DriftResult:
    """Result for one (mailbox, sender_domain) bucket."""

    __slots__ = ("is_drift", "ks_statistic", "mailbox", "n_baseline", "n_recent", "sender_domain")

    def __init__(
        self,
        mailbox: str,
        sender_domain: str,
        ks_statistic: float,
        is_drift: bool,
        n_recent: int,
        n_baseline: int,
    ) -> None:
        self.mailbox = mailbox
        self.sender_domain = sender_domain
        self.ks_statistic = ks_statistic
        self.is_drift = is_drift
        self.n_recent = n_recent
        self.n_baseline = n_baseline


async def detect_distribution_drift(
    session: AsyncSession,
    *,
    mailbox: str,
    baseline_days: int = _BASELINE_DAYS,
    recent_days: int = _RECENT_DAYS,
    threshold: float = _KS_DRIFT_THRESHOLD,
) -> list[DriftResult]:
    """Return drift results per sender_domain bucket for one mailbox."""
    now = datetime.now(UTC)
    baseline_start = now - timedelta(days=baseline_days)
    recent_start = now - timedelta(days=recent_days)

    stmt = (
        select(
            UrgencyPrediction.sender_domain,
            UrgencyPrediction.probs,
            UrgencyPrediction.created_at,
        )
        .where(
            UrgencyPrediction.mailbox == mailbox,
            UrgencyPrediction.created_at >= baseline_start,
            UrgencyPrediction.probs.is_not(None),
        )
        .order_by(UrgencyPrediction.created_at.desc())
        .limit(_PREDICTION_LOAD_CAP)
    )
    rows = (await session.execute(stmt)).all()

    # Bucket by sender_domain → {recent: [...], baseline: [...]}
    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"recent": [], "baseline": []})
    for domain, probs, created_at in rows:
        if not domain or not probs:
            continue
        val = _extract_high_prob(probs)
        if created_at >= recent_start:
            buckets[domain]["recent"].append(val)
        else:
            buckets[domain]["baseline"].append(val)

    results: list[DriftResult] = []
    for domain, pools in buckets.items():
        recent = pools["recent"]
        baseline = pools["baseline"]
        if len(recent) < _COLD_START_MIN_ROWS:
            continue
        if len(baseline) == 0:
            continue

        ks = _ks_statistic(recent, baseline)
        is_drift = ks > threshold
        result = DriftResult(
            mailbox=mailbox,
            sender_domain=domain,
            ks_statistic=ks,
            is_drift=is_drift,
            n_recent=len(recent),
            n_baseline=len(baseline),
        )
        results.append(result)
        if is_drift:
            logger.warning(
                "urgency_drift_detected",
                mailbox=mailbox,
                sender_domain=domain,
                ks_statistic=round(ks, 4),
                n_recent=len(recent),
                n_baseline=len(baseline),
            )

    return results


async def run_calibration() -> None:
    """Run drift detection for every configured mailbox."""
    settings = get_settings()
    factory = get_session_factory()
    mailboxes = settings.mailbox_list
    if not mailboxes:
        logger.info("calibration_skipped_no_mailboxes")
        return

    total_drift = 0
    for mailbox in mailboxes:
        try:
            async with factory() as session:
                results = await detect_distribution_drift(session, mailbox=mailbox)
                total_drift += sum(1 for r in results if r.is_drift)
                logger.info(
                    "calibration_mailbox_complete",
                    mailbox=mailbox,
                    buckets_checked=len(results),
                    drift_buckets=sum(1 for r in results if r.is_drift),
                )
        except Exception:
            logger.exception("calibration_mailbox_failed", mailbox=mailbox)

    logger.info("calibration_complete", total_drift_buckets=total_drift)
