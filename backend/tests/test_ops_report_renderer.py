"""PDF renderer: labels, numbers, and on-disk store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.models.schemas.ops_report import (
    CategoryCount,
    MailboxVolume,
    OpsMetricsResponse,
    OpsPeriod,
    QueueSnapshot,
    RejectThemeCount,
)
from app.services.ops_report_renderer import (
    COMPANY_NAME,
    FOOTER_LINE,
    briefing_text,
    render_pdf,
    report_filename,
    store_pdf,
)


def _metrics() -> OpsMetricsResponse:
    start = datetime(2026, 8, 3, 4, 0, tzinfo=UTC)
    end = datetime(2026, 8, 10, 3, 59, 59, tzinfo=UTC)
    return OpsMetricsResponse(
        period=OpsPeriod(date_from=start, date_to=end),
        volume_by_mailbox=[
            MailboxVolume(
                mailbox="sales",
                email="sales@example.com",
                thread_volume=12,
                awaiting_action=3,
                stale=1,
            ),
            MailboxVolume(
                mailbox="client-relations",
                email="cr@example.com",
                thread_volume=7,
                awaiting_action=2,
                stale=0,
            ),
        ],
        total_volume=19,
        spam_filtered=4,
        drafts_generated=22,
        approvals=16,
        rejects=3,
        approval_rate=16 / 19,
        top_reject_themes=[
            RejectThemeCount(reason_code="tone", count=2),
            RejectThemeCount(reason_code="factual", count=1),
        ],
        volume_by_category=[
            CategoryCount(category="billing", count=11),
            CategoryCount(category="scheduling", count=8),
        ],
        avg_resolve_hours=6.4,
        resolve_sample_count=11,
        queue=QueueSnapshot(
            awaiting_action=5,
            stale=1,
            filtered=4,
            urgency_critical=1,
            urgency_high=2,
            urgency_normal=2,
            urgency_low=0,
        ),
        generated_at=datetime(2026, 8, 12, 15, 4, tzinfo=UTC),
    )


def test_briefing_covers_period_and_live_queue() -> None:
    text = briefing_text(_metrics())
    assert "19 inbound threads" in text
    assert "4 filtered" in text
    assert "22 drafts generated" in text
    assert "84.2% approved" in text
    assert "6.4 hours" in text
    assert "5 awaiting action" in text
    assert "1 stale" in text
    assert "1 critical" in text


def test_briefing_empty_period_still_reports_queue() -> None:
    metrics = _metrics()
    metrics.total_volume = 0
    metrics.volume_by_mailbox = [
        MailboxVolume(mailbox="sales", email="sales@example.com", thread_volume=0)
    ]
    metrics.spam_filtered = 0
    metrics.drafts_generated = 0
    metrics.approvals = 0
    metrics.rejects = 0
    metrics.approval_rate = 0.0
    metrics.avg_resolve_hours = None
    metrics.resolve_sample_count = 0
    metrics.top_reject_themes = []
    metrics.volume_by_category = []
    text = briefing_text(metrics)
    assert "No inbound threads in this period." in text
    assert "No sent replies in this period." in text
    assert "Open queue now: 5 awaiting action, 1 stale." in text


def test_render_pdf_contains_company_and_numbers() -> None:
    pdf = render_pdf(_metrics())
    assert pdf.startswith(b"%PDF")
    text = pdf.decode("latin-1", errors="ignore")
    assert COMPANY_NAME.upper() in text
    assert "Weekly Operations Report" in text
    assert "THIS PERIOD" in text
    assert "OPEN QUEUE" in text
    assert "VOLUME BY MAILBOX" in text
    assert "INBOUND BY CATEGORY" in text
    assert "TOP REJECT THEMES" in text
    assert "sales@example.com" in text
    assert "Spam / no-action" in text
    assert "Drafts generated" in text
    assert "Approval rate" in text
    assert "Awaiting action" in text
    assert FOOTER_LINE in text
    assert "84.2%" in text
    assert "6.4 hours" in text
    assert "Tone" in text
    assert "Billing" in text
    assert "Critical" in text
    assert "3 August 2026" in text
    assert "9 August 2026" in text
    assert "America/New_York" not in text
    assert "11:04" in text
    assert "UTC" not in text


def test_render_pdf_empty_themes_note() -> None:
    metrics = _metrics()
    metrics.top_reject_themes = []
    metrics.volume_by_category = []
    metrics.avg_resolve_hours = None
    metrics.resolve_sample_count = 0
    metrics.approvals = 0
    metrics.rejects = 0
    metrics.approval_rate = 0.0
    pdf = render_pdf(metrics)
    text = pdf.decode("latin-1", errors="ignore")
    assert "No rejects in this period." in text
    assert "No sent replies" in text


def test_report_filename_uses_ny_calendar_dates() -> None:
    metrics = _metrics()
    assert (
        report_filename(metrics.period.date_from, metrics.period.date_to)
        == "ops-weekly-2026-08-03_2026-08-09.pdf"
    )


def test_store_pdf_writes_when_dir_configured(tmp_path: Path) -> None:
    settings = Settings(environment="local", ops_report_dir=str(tmp_path))
    metrics = _metrics()
    pdf = render_pdf(metrics)
    path = store_pdf(pdf, metrics.period.date_from, metrics.period.date_to, settings)
    assert path is not None
    assert path.exists()
    assert path.name == report_filename(metrics.period.date_from, metrics.period.date_to)
    assert path.read_bytes().startswith(b"%PDF")


def test_store_pdf_skips_when_dir_unset() -> None:
    settings = Settings(environment="local", ops_report_dir="")
    metrics = _metrics()
    path = store_pdf(b"%PDF-1.4", metrics.period.date_from, metrics.period.date_to, settings)
    assert path is None
