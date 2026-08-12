"""Leadership weekly ops report — PDF via ReportLab Platypus.

Library choice
    ReportLab (pure Python). WeasyPrint needs Pango/Cairo, which is painful in
    Docker/Lambda. xhtml2pdf's CSS support is weak. ReportLab is the standard
    for data-driven reports and needs no system libraries.

The renderer prints numbers from ``OpsMetricsResponse`` only. It never queries
the database. Call ``ops_metrics_service.get_metrics`` first.

Storage
    When ``OPS_REPORT_DIR`` is set, the weekly job writes
    ``ops-weekly-{date_from}_{date_to}.pdf``. Download endpoints generate on
    demand from the same metrics service so the file always matches the preview
    API. Report bodies are never stored in Outlook.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import Settings
from app.models.schemas.ops_report import OpsMetricsResponse
from app.models.schemas.routing import REJECT_REASON_CODES

_INK = (0.12, 0.12, 0.12)
_MUTED = (0.34, 0.34, 0.34)
_RULE = (0.28, 0.28, 0.28)

_REASON_LABELS: dict[str, str] = {
    "tone": "Tone",
    "factual": "Factual",
    "wrong_action": "Wrong action",
    "incomplete": "Incomplete",
    "policy": "Policy",
    "recipients": "Recipients",
    "other": "Other",
}

CONTENT_TYPE = "application/pdf"
COMPANY_NAME = "SampleSite Support"
FOOTER_LINE = "SampleSite Support - read-only triage. Humans send in Outlook."


class _Hairline(Flowable):
    def __init__(self, width: float, stroke: float = 0.4) -> None:
        super().__init__()
        self.width = width
        self.stroke = stroke
        self.height = 4

    def draw(self) -> None:
        self.canv.setStrokeColorRGB(*_RULE)
        self.canv.setLineWidth(self.stroke)
        self.canv.line(0, 2, self.width, 2)


class _SectionHead(Flowable):
    """Small uppercase label with a hairline — quieter than a bold heading."""

    def __init__(self, text: str, width: float) -> None:
        super().__init__()
        self.text = text.upper()
        self.width = width
        self.height = 16

    def draw(self) -> None:
        self.canv.setFillColorRGB(*_INK)
        self.canv.setFont("Times-Bold", 8)
        self.canv.drawString(0, 7, self.text)
        self.canv.setStrokeColorRGB(*_RULE)
        self.canv.setLineWidth(0.4)
        self.canv.line(0, 3, self.width, 3)


def period_calendar_dates(
    date_from: datetime,
    date_to: datetime,
    timezone_name: str = "America/New_York",
) -> tuple[date, date]:
    tz = ZoneInfo(timezone_name)
    start = date_from.replace(tzinfo=UTC) if date_from.tzinfo is None else date_from
    end = date_to.replace(tzinfo=UTC) if date_to.tzinfo is None else date_to
    return start.astimezone(tz).date(), end.astimezone(tz).date()


def report_filename(
    date_from: datetime,
    date_to: datetime,
    timezone_name: str = "America/New_York",
) -> str:
    start, end = period_calendar_dates(date_from, date_to, timezone_name)
    return f"ops-weekly-{start.isoformat()}_{end.isoformat()}.pdf"


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "kicker": ParagraphStyle(
            "kicker",
            fontName="Times-Bold",
            fontSize=8,
            leading=10,
            textColor=_MUTED,
            alignment=TA_LEFT,
            spaceAfter=6,
        ),
        "title": ParagraphStyle(
            "title",
            fontName="Times-Bold",
            fontSize=18,
            leading=22,
            textColor=_INK,
            spaceAfter=6,
        ),
        "meta": ParagraphStyle(
            "meta",
            fontName="Times-Italic",
            fontSize=9,
            leading=12,
            textColor=_MUTED,
            spaceAfter=1,
        ),
        "section": ParagraphStyle(
            "section",
            fontName="Times-Bold",
            fontSize=8,
            leading=10,
            textColor=_INK,
            spaceBefore=0,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="Times-Roman",
            fontSize=8,
            leading=11,
            textColor=_MUTED,
        ),
        "briefing": ParagraphStyle(
            "briefing",
            fontName="Times-Roman",
            fontSize=10,
            leading=14,
            textColor=_INK,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "cell": ParagraphStyle(
            "cell",
            fontName="Times-Roman",
            fontSize=9,
            leading=12,
            textColor=_INK,
        ),
        "cell_right": ParagraphStyle(
            "cell_right",
            fontName="Times-Roman",
            fontSize=9,
            leading=12,
            textColor=_INK,
            alignment=TA_RIGHT,
        ),
        "th": ParagraphStyle(
            "th",
            fontName="Times-Bold",
            fontSize=8,
            leading=11,
            textColor=_INK,
        ),
        "th_right": ParagraphStyle(
            "th_right",
            fontName="Times-Bold",
            fontSize=8,
            leading=11,
            textColor=_INK,
            alignment=TA_RIGHT,
        ),
        "empty": ParagraphStyle(
            "empty",
            fontName="Times-Italic",
            fontSize=9,
            leading=12,
            textColor=_INK,
        ),
        "footer": ParagraphStyle(
            "footer",
            fontName="Times-Roman",
            fontSize=8,
            leading=10,
            textColor=_INK,
        ),
    }


def _in_tz(value: datetime, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _fmt_dt(value: datetime, timezone_name: str = "America/New_York") -> str:
    local = _in_tz(value, timezone_name)
    return local.strftime("%d %B %Y, %H:%M").lstrip("0")


def _fmt_day(value: datetime, timezone_name: str = "America/New_York") -> str:
    local = _in_tz(value, timezone_name)
    return local.strftime("%d %B %Y").lstrip("0")


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _fmt_rate(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def _fmt_hours(hours: float | None) -> str:
    if hours is None:
        return "n/a"
    return f"{hours:.1f} hours"


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    label = singular if n == 1 else (plural or f"{singular}s")
    return f"{_fmt_int(n)} {label}"


def briefing_text(metrics: OpsMetricsResponse) -> str:
    """One-paragraph leadership summary. Always includes the live queue."""
    parts: list[str] = []
    active_boxes = sum(1 for row in metrics.volume_by_mailbox if row.thread_volume)
    if metrics.total_volume:
        box_bit = (
            f" across {_plural(active_boxes, 'mailbox', 'mailboxes')}"
            if active_boxes
            else ""
        )
        parts.append(f"{_plural(metrics.total_volume, 'inbound thread')}{box_bit}.")
    else:
        parts.append("No inbound threads in this period.")
    if metrics.spam_filtered:
        parts.append(
            f"{_fmt_int(metrics.spam_filtered)} filtered as spam or no-action."
        )
    if metrics.drafts_generated:
        parts.append(f"{_plural(metrics.drafts_generated, 'draft')} generated.")
    decided = metrics.approvals + metrics.rejects
    if decided:
        parts.append(
            f"Reviewers decided {_fmt_int(decided)} drafts "
            f"({_fmt_rate(metrics.approval_rate)} approved — "
            f"{_fmt_int(metrics.approvals)} approved, "
            f"{_fmt_int(metrics.rejects)} rejected)."
        )
    elif metrics.drafts_generated:
        parts.append("None of those drafts were approved or rejected yet.")
    if metrics.avg_resolve_hours is not None:
        parts.append(
            f"Average time to a sent reply: {_fmt_hours(metrics.avg_resolve_hours)} "
            f"(n={_fmt_int(metrics.resolve_sample_count)})."
        )
    elif metrics.resolve_sample_count == 0:
        parts.append("No sent replies in this period.")
    queue = metrics.queue
    parts.append(
        f"Open queue now: {_fmt_int(queue.awaiting_action)} awaiting action, "
        f"{_fmt_int(queue.stale)} stale."
    )
    hot = queue.urgency_critical + queue.urgency_high
    if hot:
        parts.append(
            f"{_fmt_int(queue.urgency_critical)} critical and "
            f"{_fmt_int(queue.urgency_high)} high urgency still open."
        )
    return " ".join(parts)


def _reason_label(code: str) -> str:
    if code in _REASON_LABELS:
        return _REASON_LABELS[code]
    if code in REJECT_REASON_CODES:
        return code.replace("_", " ").title()
    return code.replace("_", " ").title()


def _table(data: list[list[Paragraph]], col_widths: list[float]) -> Table:
    table = Table(data, colWidths=col_widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"),
                ("TEXTCOLOR", (0, 0), (-1, -1), _INK),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, 0), 2),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                ("TOPPADDING", (0, 1), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, _RULE),
                ("LINEBELOW", (0, -1), (-1, -1), 0.4, _RULE),
            ]
        )
    )
    return table


def _kv_table(
    rows: list[tuple[str, str]],
    styles: dict[str, ParagraphStyle],
    width: float,
) -> Table:
    data = [
        [Paragraph(label, styles["cell"]), Paragraph(value, styles["cell_right"])]
        for label, value in rows
    ]
    table = Table(data, colWidths=[width * 0.58, width * 0.42], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, _RULE),
                ("LINEBELOW", (0, -1), (-1, -1), 0.4, _RULE),
                ("LINEABOVE", (0, 0), (-1, 0), 0.5, _RULE),
            ]
        )
    )
    return table


def _period_kpis(
    metrics: OpsMetricsResponse,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> Table:
    denom = metrics.approvals + metrics.rejects
    approval_detail = (
        f"{_fmt_rate(metrics.approval_rate)}  "
        f"({_fmt_int(metrics.approvals)} / {_fmt_int(metrics.rejects)})"
        if denom
        else "n/a"
    )
    resolve_detail = (
        f"{_fmt_hours(metrics.avg_resolve_hours)}  (n={_fmt_int(metrics.resolve_sample_count)})"
        if metrics.resolve_sample_count
        else "n/a"
    )
    return _kv_table(
        [
            ("Inbound threads", _fmt_int(metrics.total_volume)),
            ("Spam / no-action", _fmt_int(metrics.spam_filtered)),
            ("Drafts generated", _fmt_int(metrics.drafts_generated)),
            ("Approval rate", approval_detail),
            ("Time to resolve", resolve_detail),
        ],
        styles,
        width,
    )


def _queue_kpis(
    metrics: OpsMetricsResponse,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> Table:
    queue = metrics.queue
    return _kv_table(
        [
            ("Awaiting action", _fmt_int(queue.awaiting_action)),
            ("Stale", _fmt_int(queue.stale)),
            ("Critical", _fmt_int(queue.urgency_critical)),
            ("High", _fmt_int(queue.urgency_high)),
            ("Normal / low", _fmt_int(queue.urgency_normal + queue.urgency_low)),
        ],
        styles,
        width,
    )


def _pair(left: list[Flowable], right: list[Flowable], usable: float) -> Table:
    gap = 0.28 * inch
    col = (usable - gap) / 2
    table = Table(
        [[left, "", right]],
        colWidths=[col, gap, col],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _volume_table(metrics: OpsMetricsResponse, styles: dict[str, ParagraphStyle]) -> Table:
    header = [
        Paragraph("Mailbox", styles["th"]),
        Paragraph("Inbound", styles["th_right"]),
        Paragraph("Awaiting", styles["th_right"]),
        Paragraph("Stale", styles["th_right"]),
    ]
    data: list[list[Paragraph]] = [header]
    for row in metrics.volume_by_mailbox:
        data.append(
            [
                Paragraph(
                    f"{row.mailbox}<br/><font size='8'>{row.email}</font>",
                    styles["cell"],
                ),
                Paragraph(_fmt_int(row.thread_volume), styles["cell_right"]),
                Paragraph(_fmt_int(row.awaiting_action), styles["cell_right"]),
                Paragraph(_fmt_int(row.stale), styles["cell_right"]),
            ]
        )
    data.append(
        [
            Paragraph("Total", styles["th"]),
            Paragraph(_fmt_int(metrics.total_volume), styles["th_right"]),
            Paragraph(_fmt_int(metrics.queue.awaiting_action), styles["th_right"]),
            Paragraph(_fmt_int(metrics.queue.stale), styles["th_right"]),
        ]
    )
    return _table(data, [3.1 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch])


def _count_table(
    rows: list[tuple[str, int]],
    styles: dict[str, ParagraphStyle],
    left_header: str,
    width: float | None = None,
) -> Table:
    header = [
        Paragraph(left_header, styles["th"]),
        Paragraph("Count", styles["th_right"]),
    ]
    data: list[list[Paragraph]] = [header]
    for label, count in rows:
        data.append(
            [
                Paragraph(label, styles["cell"]),
                Paragraph(_fmt_int(count), styles["cell_right"]),
            ]
        )
    left = (width or 7.0 * inch) - 1.15 * inch
    return _table(data, [left, 1.15 * inch])


def _themes_table(
    metrics: OpsMetricsResponse,
    styles: dict[str, ParagraphStyle],
    width: float | None = None,
) -> Table:
    return _count_table(
        [(_reason_label(row.reason_code), row.count) for row in metrics.top_reject_themes],
        styles,
        "Reason",
        width,
    )


def _category_table(
    metrics: OpsMetricsResponse,
    styles: dict[str, ParagraphStyle],
    width: float | None = None,
) -> Table:
    return _count_table(
        [(row.category.replace("_", " ").title(), row.count) for row in metrics.volume_by_category],
        styles,
        "Category",
        width,
    )


def _draw_footer(canvas: Canvas, doc: SimpleDocTemplate) -> None:
    canvas.saveState()
    canvas.setStrokeColorRGB(*_RULE)
    canvas.setLineWidth(0.4)
    y = 0.55 * inch
    canvas.line(doc.leftMargin, y + 12, doc.pagesize[0] - doc.rightMargin, y + 12)
    canvas.setFont("Times-Roman", 8)
    canvas.setFillColorRGB(*_INK)
    canvas.drawString(doc.leftMargin, y, FOOTER_LINE)
    canvas.drawRightString(
        doc.pagesize[0] - doc.rightMargin,
        y,
        f"Page {doc.page}",
    )
    canvas.restoreState()


def render_pdf(
    metrics: OpsMetricsResponse,
    timezone_name: str = "America/New_York",
) -> bytes:
    """Build a one-to-two page letter PDF from already-computed metrics."""
    styles = _styles()
    buffer = BytesIO()
    usable = letter[0] - 1.5 * inch
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.75 * inch,
        title=f"{COMPANY_NAME} Weekly Operations Report",
        author=COMPANY_NAME,
        pageCompression=0,
    )
    period = metrics.period
    gap = 0.28 * inch
    col = (usable - gap) / 2
    category_body: Flowable = (
        _category_table(metrics, styles, col)
        if metrics.volume_by_category
        else Paragraph("No inbound threads in this period.", styles["empty"])
    )
    themes_body: Flowable = (
        _themes_table(metrics, styles, col)
        if metrics.top_reject_themes
        else Paragraph("No rejects in this period.", styles["empty"])
    )
    story: list[Flowable] = [
        Paragraph(COMPANY_NAME.upper(), styles["kicker"]),
        Paragraph("Weekly Operations Report", styles["title"]),
        Paragraph(
            f"{_fmt_day(period.date_from, timezone_name)} through "
            f"{_fmt_day(period.date_to, timezone_name)}",
            styles["meta"],
        ),
        Paragraph(
            f"Generated {_fmt_dt(metrics.generated_at, timezone_name)}",
            styles["meta"],
        ),
        Spacer(1, 10),
        _Hairline(usable, 0.6),
        Paragraph(briefing_text(metrics), styles["briefing"]),
        Spacer(1, 14),
        _pair(
            [
                _SectionHead("This period", col),
                Spacer(1, 6),
                _period_kpis(metrics, styles, col),
            ],
            [
                _SectionHead("Open queue", col),
                Spacer(1, 6),
                _queue_kpis(metrics, styles, col),
            ],
            usable,
        ),
        Spacer(1, 16),
        _SectionHead("Volume by mailbox", usable),
        Spacer(1, 6),
        _volume_table(metrics, styles)
        if metrics.volume_by_mailbox
        else Paragraph("No configured mailboxes in this window.", styles["empty"]),
        Spacer(1, 16),
        _pair(
            [
                _SectionHead("Inbound by category", col),
                Spacer(1, 6),
                category_body,
            ],
            [
                _SectionHead("Top reject themes", col),
                Spacer(1, 6),
                themes_body,
            ],
            usable,
        ),
        Spacer(1, 18),
        _SectionHead("Definitions", usable),
        Spacer(1, 6),
        Paragraph(
            "Inbound counts threads with at least one inbound message in the period. "
            "Spam / no-action counts threads that entered SPAM or NO_ACTION. "
            "Drafts generated counts drafts created in the period. "
            "Approval rate is approvals divided by approvals plus rejects. "
            "Time to resolve is sent-reply time minus the thread's first inbound "
            "message. Awaiting and stale are the live queue at generation time. "
            "Stale means awaiting action with no inbound message in the last 24 hours.",
            styles["body"],
        ),
    ]
    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buffer.getvalue()


def store_pdf(
    pdf_bytes: bytes,
    date_from: datetime,
    date_to: datetime,
    settings: Settings,
    timezone_name: str = "America/New_York",
) -> Path | None:
    """Write the artifact when ``OPS_REPORT_DIR`` is configured. None if unset."""
    raw = settings.ops_report_dir.strip()
    if not raw:
        return None
    directory = Path(raw).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / report_filename(date_from, date_to, timezone_name)
    path.write_bytes(pdf_bytes)
    return path


async def render_pdf_async(
    metrics: OpsMetricsResponse,
    timezone_name: str = "America/New_York",
) -> bytes:
    return await asyncio.to_thread(render_pdf, metrics, timezone_name)


async def store_pdf_async(
    pdf_bytes: bytes,
    date_from: datetime,
    date_to: datetime,
    settings: Settings,
    timezone_name: str = "America/New_York",
) -> Path | None:
    return await asyncio.to_thread(
        store_pdf, pdf_bytes, date_from, date_to, settings, timezone_name
    )
