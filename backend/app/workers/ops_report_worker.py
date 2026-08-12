"""Weekly ops report scheduler entry — APScheduler / EventBridge-safe.

Does not import or call Microsoft Graph. Delivery is local PDF store plus
optional SMTP configured separately from Graph.
"""

from __future__ import annotations

import structlog

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.services import ops_metrics_service
from app.services.ops_report_job import generate_ops_report

logger = structlog.get_logger(__name__)


async def run_weekly_ops_report() -> None:
    """Generate last week's report. Logs failures; does not raise to the scheduler."""
    settings = get_settings()
    date_from, date_to = ops_metrics_service.last_calendar_week(settings.ops_report_timezone)
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            _pdf, result = await generate_ops_report(
                session,
                settings,
                date_from,
                date_to,
                persist=True,
                send_email=settings.ops_report_email_enabled,
            )
        logger.info(
            "ops_report_generated",
            filename=result.filename,
            stored=result.stored,
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
        )
    except Exception:
        logger.exception(
            "ops_report_job_failed",
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
        )
