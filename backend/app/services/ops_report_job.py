"""Generate, store, and optionally email the weekly ops report.

Email uses stdlib SMTP only — never Microsoft Graph sendMail. Disabled unless
``ops_report_email_enabled`` is true and SMTP host + recipients are set.
"""

from __future__ import annotations

import asyncio
import smtplib
from datetime import datetime
from email.message import EmailMessage

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.schemas.ops_report import OpsMetricsResponse, OpsReportGenerateResponse
from app.services import ops_metrics_service
from app.services.ops_report_renderer import (
    CONTENT_TYPE,
    briefing_text,
    period_calendar_dates,
    render_pdf_async,
    report_filename,
    store_pdf_async,
)

logger = structlog.get_logger(__name__)


def _smtp_recipients(settings: Settings) -> list[str]:
    return [part.strip() for part in settings.ops_report_smtp_to.split(",") if part.strip()]


def _summary_body(metrics: OpsMetricsResponse, timezone_name: str) -> str:
    period = metrics.period
    start, end = period_calendar_dates(period.date_from, period.date_to, timezone_name)
    return (
        "SampleSite Support — weekly operations report\n\n"
        f"Period: {start.isoformat()} through {end.isoformat()}\n\n"
        f"{briefing_text(metrics)}\n\n"
        "Read-only triage. Humans send in Outlook.\n"
    )


def _send_smtp(
    settings: Settings,
    metrics: OpsMetricsResponse,
    pdf_bytes: bytes,
    filename: str,
) -> None:
    recipients = _smtp_recipients(settings)
    from_addr = settings.ops_report_smtp_from.strip() or settings.ops_report_smtp_user.strip()
    tz_name = settings.ops_report_timezone
    start, end = period_calendar_dates(
        metrics.period.date_from, metrics.period.date_to, tz_name
    )
    message = EmailMessage()
    message["Subject"] = f"Weekly ops report {start.isoformat()} – {end.isoformat()}"
    message["From"] = from_addr
    message["To"] = ", ".join(recipients)
    message.set_content(_summary_body(metrics, tz_name))
    message.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=filename,
    )
    host = settings.ops_report_smtp_host
    port = settings.ops_report_smtp_port
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.ehlo()
        if settings.ops_report_smtp_port != 25:
            smtp.starttls()
            smtp.ehlo()
        user = settings.ops_report_smtp_user.strip()
        if user:
            smtp.login(user, settings.ops_report_smtp_password)
        smtp.send_message(message)


async def maybe_send_email(
    settings: Settings,
    metrics: OpsMetricsResponse,
    pdf_bytes: bytes,
    filename: str,
) -> bool:
    """Send via SMTP when enabled and configured. Never uses Graph. Returns sent?"""
    if not settings.ops_report_email_enabled:
        logger.info("ops_report_email_disabled")
        return False
    if not settings.ops_report_smtp_host.strip() or not _smtp_recipients(settings):
        logger.info("ops_report_email_disabled", reason="smtp_not_configured")
        return False
    await asyncio.to_thread(_send_smtp, settings, metrics, pdf_bytes, filename)
    logger.info("ops_report_email_sent", recipient_count=len(_smtp_recipients(settings)))
    return True


async def generate_ops_report(
    session: AsyncSession,
    settings: Settings,
    date_from: datetime,
    date_to: datetime,
    *,
    mailbox: str | None = None,
    persist: bool = True,
    send_email: bool = False,
    generated_by: str | None = None,
) -> tuple[bytes, OpsReportGenerateResponse]:
    """Metrics → PDF → optional disk store → optional SMTP.

    Callers that only need bytes (authenticated download) pass persist=False.
    """
    metrics = await ops_metrics_service.get_metrics(
        session,
        settings,
        date_from,
        date_to,
        mailbox=mailbox,
    )
    tz_name = settings.ops_report_timezone
    pdf_bytes = await render_pdf_async(
        metrics, timezone_name=tz_name, generated_by=generated_by
    )
    filename = report_filename(
        metrics.period.date_from, metrics.period.date_to, timezone_name=tz_name
    )
    stored = False
    if persist:
        path = await store_pdf_async(
            pdf_bytes,
            metrics.period.date_from,
            metrics.period.date_to,
            settings,
            timezone_name=tz_name,
        )
        stored = path is not None
    if send_email:
        await maybe_send_email(settings, metrics, pdf_bytes, filename)
    return pdf_bytes, OpsReportGenerateResponse(
        filename=filename,
        stored=stored,
        content_type=CONTENT_TYPE,
        period=metrics.period,
        generated_at=metrics.generated_at,
    )
