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
from collections.abc import Callable
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib.colors import Color
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepTogether,
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
_MUTED = (0.42, 0.42, 0.42)
_RULE = (0.22, 0.22, 0.22)
_GRID = (0.91, 0.91, 0.91)
_AXIS = (0.72, 0.72, 0.72)
_HAIRLINE = (0.78, 0.78, 0.78)
_PAPER = (1.0, 1.0, 1.0)
_HAIRLINE_GAP = 8.0

# Print-safe chart fills only. Matches dashboard urgency/state tones, muted.
_CHART_SLATE = (0.22, 0.28, 0.36)
_CHART_BLUE = (0.36, 0.51, 0.70)
_CHART_AMBER = (0.80, 0.58, 0.22)
_CHART_RED = (0.72, 0.30, 0.28)
_CHART_GREEN = (0.38, 0.58, 0.46)

_SERIES_INBOUND = _CHART_SLATE
_SERIES_AWAITING = _CHART_BLUE
_SERIES_STALE = _CHART_AMBER

_SLICES = (
    _CHART_RED,
    _CHART_AMBER,
    _CHART_BLUE,
    _CHART_GREEN,
)

MISSING = "-"

_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONTS_REGISTERED = False

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
FOOTER_CONFIDENTIAL = "This is an internal confidential document."

FONT_REGULAR = "Inter"
FONT_BOLD = "Inter-Bold"
FONT_ITALIC = "Inter-Italic"

Rgb = tuple[float, float, float]
SliceRow = tuple[str, int, Rgb]


def _ensure_fonts() -> None:
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(_FONT_DIR / "Inter-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(_FONT_DIR / "Inter-Bold.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_ITALIC, str(_FONT_DIR / "Inter-Italic.ttf")))
    pdfmetrics.registerFont(TTFont("Inter-BoldItalic", str(_FONT_DIR / "Inter-BoldItalic.ttf")))
    pdfmetrics.registerFontFamily(
        "Inter",
        normal=FONT_REGULAR,
        bold=FONT_BOLD,
        italic=FONT_ITALIC,
        boldItalic="Inter-BoldItalic",
    )
    _FONTS_REGISTERED = True


def _nice_ceiling(value: int) -> int:
    """Axis max that sits above ``value`` on a 4-tick scale."""
    if value <= 0:
        return 4
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        top = step * 4
        if top >= value:
            return top
    return value


class _SectionHead(Flowable):
    """Small uppercase label — type only, no rule."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text.upper()
        self.height = 14

    def wrap(self, availWidth: float, _availHeight: float) -> tuple[float, float]:
        self.width = availWidth
        return availWidth, self.height

    def draw(self) -> None:
        self.canv.setFillColorRGB(*_INK)
        self.canv.setFont(FONT_BOLD, 8)
        self.canv.drawString(0, 3, self.text)


class _KpiCell(Flowable):
    """Glanceable number + uppercase label for the snapshot strip."""

    def __init__(self, value: str, label: str, width: float) -> None:
        super().__init__()
        self.value = value
        self.label = label.upper()
        self.width = width
        self.height = 36

    def draw(self) -> None:
        self.canv.setFillColorRGB(*_INK)
        self.canv.setFont(FONT_BOLD, 16)
        self.canv.drawString(0, 16, self.value)
        self.canv.setFillColorRGB(*_MUTED)
        self.canv.setFont(FONT_REGULAR, 7)
        self.canv.drawString(0, 4, self.label)


class _ChartLegend(Flowable):
    def __init__(self, items: list[tuple[str, Rgb]], width: float) -> None:
        super().__init__()
        self.items = items
        self.width = width
        self.height = 12

    def wrap(self, _availWidth: float, _availHeight: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        x = 0.0
        for label, color in self.items:
            self.canv.setFillColorRGB(*color)
            self.canv.rect(x, 2, 7, 7, fill=1, stroke=0)
            self.canv.setFillColorRGB(*_INK)
            self.canv.setFont(FONT_REGULAR, 7)
            self.canv.drawString(x + 10, 3, label)
            x += 10 + self.canv.stringWidth(label, FONT_REGULAR, 7) + 14


class _GroupedBarChart(Flowable):
    """Vertical clustered bars with a y-axis, ticks, and grid."""

    def __init__(
        self,
        groups: list[tuple[str, list[int]]],
        series: list[tuple[str, Rgb]],
        width: float,
        height: float = 2.35 * inch,
    ) -> None:
        super().__init__()
        self.groups = groups
        self.series = series
        self.width = width
        self.height = height

    def wrap(self, _availWidth: float, _availHeight: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        left, bottom, top, right = 28.0, 22.0, 6.0, 4.0
        plot_w = self.width - left - right
        plot_h = self.height - bottom - top
        peaks = [max(values) if values else 0 for _, values in self.groups]
        ymax = _nice_ceiling(max(peaks, default=0))

        for tick in range(5):
            y = bottom + plot_h * (tick / 4)
            self.canv.setStrokeColorRGB(*(_AXIS if tick == 0 else _GRID))
            self.canv.setLineWidth(0.5 if tick == 0 else 0.35)
            self.canv.line(left, y, left + plot_w, y)
            self.canv.setFillColorRGB(*_MUTED)
            self.canv.setFont(FONT_REGULAR, 7)
            self.canv.drawRightString(left - 5, y - 2, f"{int(ymax * tick / 4):,}")

        if not self.groups:
            self.canv.setFillColorRGB(*_MUTED)
            self.canv.setFont(FONT_REGULAR, 11)
            self.canv.drawCentredString(left + plot_w / 2, bottom + plot_h / 2, MISSING)
            return

        n_groups = len(self.groups)
        n_series = max(len(self.series), 1)
        group_w = plot_w / n_groups
        cluster_w = group_w * 0.72
        bar_w = cluster_w / n_series
        gap = 1.4

        for gi, (label, values) in enumerate(self.groups):
            cluster_left = left + gi * group_w + (group_w - cluster_w) / 2
            for si, raw in enumerate(values):
                color = self.series[si][1] if si < len(self.series) else _INK
                h = 0.0 if ymax == 0 else plot_h * (raw / ymax)
                x = cluster_left + si * bar_w
                if h <= 0:
                    continue
                self.canv.setFillColorRGB(*color)
                self.canv.rect(x, bottom, max(bar_w - gap, 1.5), h, fill=1, stroke=0)
            self.canv.setFillColorRGB(*_INK)
            self.canv.setFont(FONT_REGULAR, 7)
            self.canv.drawCentredString(
                left + gi * group_w + group_w / 2,
                6,
                label,
            )


class _DonutChart(Flowable):
    """Composition chart: slice mix plus count / percent legend."""

    def __init__(
        self,
        slices: list[SliceRow],
        width: float,
        height: float = 2.15 * inch,
        center_label: str = "Total",
    ) -> None:
        super().__init__()
        self.slices = slices
        self.width = width
        self.height = height
        self.center_label = center_label.upper()

    def wrap(self, _availWidth: float, _availHeight: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        total = sum(value for _, value, _ in self.slices)
        radius = min(self.height * 0.38, self.width * 0.22)
        cx = radius + 10
        cy = self.height / 2

        if total <= 0:
            self.canv.setStrokeColorRGB(*_GRID)
            self.canv.setLineWidth(10)
            self.canv.circle(cx, cy, radius * 0.72, fill=0, stroke=1)
            self.canv.setFillColorRGB(*_MUTED)
            self.canv.setFont(FONT_BOLD, 12)
            self.canv.drawCentredString(cx, cy - 4, MISSING)
        else:
            start = 90.0
            for _label, value, color in self.slices:
                if value <= 0:
                    continue
                self.canv.setFillColorRGB(*color)
                if value == total:
                    self.canv.circle(cx, cy, radius, fill=1, stroke=0)
                    break
                extent = 360.0 * value / total
                self.canv.wedge(
                    cx - radius,
                    cy - radius,
                    cx + radius,
                    cy + radius,
                    start,
                    extent,
                    fill=1,
                    stroke=0,
                )
                start += extent
            self.canv.setFillColorRGB(*_PAPER)
            self.canv.circle(cx, cy, radius * 0.58, fill=1, stroke=0)
            self.canv.setFillColorRGB(*_INK)
            self.canv.setFont(FONT_BOLD, 11)
            self.canv.drawCentredString(cx, cy + 2, _fmt_int(total))
            self.canv.setFillColorRGB(*_MUTED)
            self.canv.setFont(FONT_REGULAR, 6)
            self.canv.drawCentredString(cx, cy - 9, self.center_label)

        legend_x = cx + radius + 14
        row_h = 15
        legend_h = max(len(self.slices), 1) * row_h
        y = cy + legend_h / 2 - 10
        for label, value, color in self.slices:
            self.canv.setFillColorRGB(*color)
            self.canv.rect(legend_x, y, 7, 7, fill=1, stroke=0)
            self.canv.setFillColorRGB(*_INK)
            self.canv.setFont(FONT_REGULAR, 8)
            self.canv.drawString(legend_x + 11, y, label)
            pct = MISSING if total <= 0 else f"{100 * value / total:.0f}%"
            self.canv.setFont(FONT_BOLD, 8)
            self.canv.drawRightString(self.width - 2, y, f"{_fmt_int(value)}  {pct}")
            y -= row_h


class _HBarAxisChart(Flowable):
    """Horizontal bars against a shared x-axis, with count and share of total."""

    _ROW = 22
    _LABEL_W = 1.05 * inch
    _VALUE_W = 0.7 * inch

    def __init__(
        self,
        rows: list[tuple[str, int]],
        width: float,
        *,
        show_share: bool = True,
        fill: Rgb = _CHART_SLATE,
    ) -> None:
        super().__init__()
        self.rows = rows
        self.width = width
        self.show_share = show_share
        self.fill = fill
        self.height = max(len(rows), 1) * self._ROW + 18

    def wrap(self, _availWidth: float, _availHeight: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        if not self.rows:
            self.canv.setFillColorRGB(*_MUTED)
            self.canv.setFont(FONT_REGULAR, 11)
            self.canv.drawCentredString(self.width / 2, self.height / 2, MISSING)
            return

        total = sum(value for _, value in self.rows)
        ymax = _nice_ceiling(max(value for _, value in self.rows))
        bar_left = self._LABEL_W
        bar_w = max(self.width - bar_left - self._VALUE_W, 0.7 * inch)
        axis_y = 12

        self.canv.setStrokeColorRGB(*_AXIS)
        self.canv.setLineWidth(0.5)
        self.canv.line(bar_left, axis_y, bar_left + bar_w, axis_y)
        for tick in range(5):
            x = bar_left + bar_w * (tick / 4)
            self.canv.line(x, axis_y, x, axis_y + 3)
            self.canv.setFillColorRGB(*_MUTED)
            self.canv.setFont(FONT_REGULAR, 6)
            self.canv.drawCentredString(x, 2, f"{int(ymax * tick / 4):,}")

        for index, (label, value) in enumerate(self.rows):
            y = self.height - (index + 1) * self._ROW + 4
            self.canv.setFillColorRGB(*_INK)
            self.canv.setFont(FONT_REGULAR, 8)
            self.canv.drawString(0, y + 2, label)
            fill_w = 0.0 if ymax == 0 else bar_w * (value / ymax)
            if fill_w > 0:
                self.canv.setFillColorRGB(*self.fill)
                self.canv.rect(bar_left, y, max(fill_w, 1.5), 9, fill=1, stroke=0)
            pct = MISSING if total <= 0 else f"{100 * value / total:.0f}%"
            self.canv.setFillColorRGB(*_INK)
            self.canv.setFont(FONT_REGULAR, 8)
            value_text = f"{_fmt_int(value)}  {pct}" if self.show_share else _fmt_int(value)
            self.canv.drawRightString(self.width, y + 2, value_text)


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
            fontName=FONT_BOLD,
            fontSize=8,
            leading=10,
            textColor=_MUTED,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "title": ParagraphStyle(
            "title",
            fontName=FONT_BOLD,
            fontSize=20,
            leading=24,
            textColor=_INK,
            spaceAfter=4,
        ),
        "meta": ParagraphStyle(
            "meta",
            fontName=FONT_ITALIC,
            fontSize=9,
            leading=12,
            textColor=_MUTED,
            spaceAfter=0,
        ),
        "caption": ParagraphStyle(
            "caption",
            fontName=FONT_REGULAR,
            fontSize=7.5,
            leading=10,
            textColor=_MUTED,
            spaceBefore=0,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            fontName=FONT_REGULAR,
            fontSize=8,
            leading=11,
            textColor=_MUTED,
        ),
        "briefing": ParagraphStyle(
            "briefing",
            fontName=FONT_REGULAR,
            fontSize=10,
            leading=14,
            textColor=_INK,
            spaceBefore=0,
            spaceAfter=2,
        ),
        "cell": ParagraphStyle(
            "cell",
            fontName=FONT_REGULAR,
            fontSize=9,
            leading=12,
            textColor=_INK,
        ),
        "cell_right": ParagraphStyle(
            "cell_right",
            fontName=FONT_REGULAR,
            fontSize=9,
            leading=12,
            textColor=_INK,
            alignment=TA_RIGHT,
        ),
        "th": ParagraphStyle(
            "th",
            fontName=FONT_BOLD,
            fontSize=8,
            leading=11,
            textColor=_MUTED,
        ),
        "th_right": ParagraphStyle(
            "th_right",
            fontName=FONT_BOLD,
            fontSize=8,
            leading=11,
            textColor=_MUTED,
            alignment=TA_RIGHT,
        ),
        "footer": ParagraphStyle(
            "footer",
            fontName=FONT_REGULAR,
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
        return MISSING
    return f"{hours:.1f} hours"


def _fmt_hours_short(hours: float | None) -> str:
    if hours is None:
        return MISSING
    return f"{hours:.1f}h"


def _fmt_approval(metrics: OpsMetricsResponse) -> str:
    if metrics.approvals + metrics.rejects == 0:
        return MISSING
    return _fmt_rate(metrics.approval_rate)


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    label = singular if n == 1 else (plural or f"{singular}s")
    return f"{_fmt_int(n)} {label}"


def briefing_text(metrics: OpsMetricsResponse) -> str:
    """One-paragraph leadership summary. Always includes the live queue."""
    parts: list[str] = []
    active_boxes = sum(1 for row in metrics.volume_by_mailbox if row.thread_volume)
    if metrics.total_volume:
        box_bit = f" across {_plural(active_boxes, 'mailbox', 'mailboxes')}" if active_boxes else ""
        parts.append(f"{_plural(metrics.total_volume, 'inbound thread')}{box_bit}.")
    else:
        parts.append("No inbound threads in this period.")
    if metrics.spam_filtered:
        parts.append(f"{_fmt_int(metrics.spam_filtered)} filtered as spam or no-action.")
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
                ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
                ("TEXTCOLOR", (0, 0), (-1, -1), _INK),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, 0), 0),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 1), (-1, -2), 7),
                ("BOTTOMPADDING", (0, 1), (-1, -2), 7),
                ("TOPPADDING", (0, -1), (-1, -1), 8),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 2),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, _INK),
                ("LINEABOVE", (0, -1), (-1, -1), 0.45, _RULE),
            ]
        )
    )
    return table


def _kpi_strip(metrics: OpsMetricsResponse, usable: float) -> Table:
    cells = [
        _KpiCell(_fmt_int(metrics.total_volume), "Inbound threads", usable / 5),
        _KpiCell(_fmt_int(metrics.drafts_generated), "Drafts generated", usable / 5),
        _KpiCell(_fmt_approval(metrics), "Approval rate", usable / 5),
        _KpiCell(
            _fmt_hours_short(metrics.avg_resolve_hours),
            "Time to resolve",
            usable / 5,
        ),
        _KpiCell(
            _fmt_int(metrics.queue.awaiting_action),
            "Awaiting action",
            usable / 5,
        ),
    ]
    col = usable / 5
    table = Table([cells], colWidths=[col] * 5, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _pair(left: list[Flowable], right: list[Flowable], usable: float) -> Table:
    gap = 0.32 * inch
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
                    f"{row.mailbox}<br/><font size='8' color='#6B6B6B'>{row.email}</font>",
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


def _mailbox_groups(metrics: OpsMetricsResponse) -> list[tuple[str, list[int]]]:
    return [
        (row.mailbox, [row.thread_volume, row.awaiting_action, row.stale])
        for row in metrics.volume_by_mailbox
    ]


def _urgency_slices(metrics: OpsMetricsResponse) -> list[SliceRow]:
    queue = metrics.queue
    return [
        ("Critical", queue.urgency_critical, _SLICES[0]),
        ("High", queue.urgency_high, _SLICES[1]),
        ("Normal", queue.urgency_normal, _SLICES[2]),
        ("Low", queue.urgency_low, _SLICES[3]),
    ]


def _pipeline_rows(metrics: OpsMetricsResponse) -> list[tuple[str, int]]:
    return [
        ("Inbound", metrics.total_volume),
        ("Filtered", metrics.spam_filtered),
        ("Drafts", metrics.drafts_generated),
        ("Reviewed", metrics.approvals + metrics.rejects),
        ("Resolved", metrics.resolve_sample_count),
    ]


def _theme_rows(metrics: OpsMetricsResponse) -> list[tuple[str, int]]:
    return [(_reason_label(row.reason_code), row.count) for row in metrics.top_reject_themes]


def _hairline(width: float, gap: float = _HAIRLINE_GAP) -> list[Flowable]:
    return [
        Spacer(1, gap),
        HRFlowable(
            width=width,
            thickness=0.4,
            color=Color(*_HAIRLINE),
            spaceBefore=0,
            spaceAfter=0,
            hAlign="LEFT",
        ),
        Spacer(1, gap),
    ]


def _footer_callback(generated_by: str | None) -> Callable[[Canvas, SimpleDocTemplate], None]:
    def _draw_footer(canvas: Canvas, doc: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setFillColorRGB(*_MUTED)
        canvas.setFont(FONT_REGULAR, 8)
        left = doc.leftMargin
        right = doc.pagesize[0] - doc.rightMargin
        if generated_by:
            canvas.drawString(left, 0.58 * inch, f"Generated by: {generated_by}")
            canvas.drawRightString(right, 0.58 * inch, f"Page {doc.page}")
            canvas.drawString(left, 0.44 * inch, FOOTER_CONFIDENTIAL)
        else:
            canvas.drawString(left, 0.55 * inch, FOOTER_CONFIDENTIAL)
            canvas.drawRightString(right, 0.55 * inch, f"Page {doc.page}")
        canvas.restoreState()

    return _draw_footer


def render_pdf(
    metrics: OpsMetricsResponse,
    timezone_name: str = "America/New_York",
    generated_by: str | None = None,
) -> bytes:
    """Build a one-to-two page letter PDF from already-computed metrics."""
    _ensure_fonts()
    styles = _styles()
    buffer = BytesIO()
    usable = letter[0] - 1.5 * inch
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.82 * inch,
        title=f"{COMPANY_NAME} Weekly Operations Report",
        author=COMPANY_NAME,
        pageCompression=0,
    )
    period = metrics.period
    gap = 0.32 * inch
    col = (usable - gap) / 2
    mailbox_series: list[tuple[str, Rgb]] = [
        ("Inbound", _SERIES_INBOUND),
        ("Awaiting", _SERIES_AWAITING),
        ("Stale", _SERIES_STALE),
    ]
    theme_rows = _theme_rows(metrics)
    mailbox_groups = _mailbox_groups(metrics)
    footer = _footer_callback(generated_by)

    story: list[Flowable] = [
        Paragraph(COMPANY_NAME.upper(), styles["kicker"]),
        Paragraph("Weekly Operations Report", styles["title"]),
        Paragraph(
            f"{_fmt_day(period.date_from, timezone_name)} - "
            f"{_fmt_day(period.date_to, timezone_name)}"
            f"  ·  Generated {_fmt_dt(metrics.generated_at, timezone_name)}",
            styles["meta"],
        ),
        *_hairline(usable),
        Paragraph(briefing_text(metrics), styles["briefing"]),
        _SectionHead("Snapshot"),
        _kpi_strip(metrics, usable),
        *_hairline(usable),
        _SectionHead("Volume by mailbox"),
        Paragraph(
            "Inbound threads this period against the live awaiting and stale queue.",
            styles["caption"],
        ),
        _ChartLegend(mailbox_series, usable),
        Spacer(1, 4),
        _GroupedBarChart(mailbox_groups, mailbox_series, usable)
        if mailbox_groups
        else _GroupedBarChart([], mailbox_series, usable),
        *_hairline(usable),
        _pair(
            [
                _SectionHead("Period pipeline"),
                Paragraph(
                    "How this period's mail moved through triage.",
                    styles["caption"],
                ),
                _HBarAxisChart(_pipeline_rows(metrics), col, show_share=False, fill=_CHART_SLATE),
            ],
            [
                _SectionHead("Open queue mix"),
                Paragraph(
                    "Live urgency mix at generation time, not limited to the period.",
                    styles["caption"],
                ),
                _DonutChart(_urgency_slices(metrics), col, center_label="Open"),
            ],
            usable,
        ),
        *_hairline(usable),
        KeepTogether(
            [
                _SectionHead("Mailbox detail"),
                Spacer(1, 4),
                _volume_table(metrics, styles)
                if metrics.volume_by_mailbox
                else Paragraph(MISSING, styles["body"]),
            ]
        ),
    ]
    if theme_rows:
        story.extend(
            [
                *_hairline(usable),
                KeepTogether(
                    [
                        _SectionHead("Reject themes"),
                        Paragraph(
                            "Share of rejected drafts this period, by reviewer reason.",
                            styles["caption"],
                        ),
                        _HBarAxisChart(theme_rows, usable, fill=_CHART_RED),
                    ]
                ),
            ]
        )
    story.extend(
        [
            *_hairline(usable),
            KeepTogether(
                [
                    _SectionHead("Definitions"),
                    Spacer(1, 4),
                    Paragraph(
                        "Inbound counts threads with at least one inbound message in the period. "
                        "Spam / no-action counts threads that entered SPAM or NO_ACTION. "
                        "Drafts generated counts drafts created in the period. "
                        "Approval rate is approvals divided by approvals plus rejects. "
                        "Time to resolve is sent-reply time minus the thread's first inbound "
                        "message. Awaiting and stale are the live queue at generation time. "
                        "Stale means awaiting action with no inbound message in the last "
                        "24 hours. Reviewed is approvals plus rejects. Resolved is sent "
                        "replies in the period.",
                        styles["body"],
                    ),
                ]
            ),
        ]
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
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
    generated_by: str | None = None,
) -> bytes:
    return await asyncio.to_thread(render_pdf, metrics, timezone_name, generated_by)


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
