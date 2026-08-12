"""Weekly ops report request/response schemas.

Field names stay stable even if a metric formula is later revised.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OpsPeriod(BaseModel):
    """Inclusive window. Query dates are New York calendar days; stored UTC."""

    model_config = ConfigDict(populate_by_name=True)

    date_from: datetime = Field(alias="from")
    date_to: datetime = Field(alias="to")


class MailboxVolume(BaseModel):
    mailbox: str
    email: str
    thread_volume: int
    awaiting_action: int = 0
    stale: int = 0


class RejectThemeCount(BaseModel):
    reason_code: str
    count: int


class CategoryCount(BaseModel):
    category: str
    count: int


class QueueSnapshot(BaseModel):
    """Open work as of report generation, not limited to the period window."""

    awaiting_action: int = 0
    stale: int = 0
    filtered: int = 0
    urgency_critical: int = 0
    urgency_high: int = 0
    urgency_normal: int = 0
    urgency_low: int = 0


class OpsMetricsResponse(BaseModel):
    period: OpsPeriod
    volume_by_mailbox: list[MailboxVolume]
    total_volume: int
    spam_filtered: int
    drafts_generated: int = 0
    approvals: int
    rejects: int
    approval_rate: float
    top_reject_themes: list[RejectThemeCount]
    volume_by_category: list[CategoryCount] = Field(default_factory=list)
    avg_resolve_hours: float | None
    resolve_sample_count: int
    queue: QueueSnapshot = Field(default_factory=QueueSnapshot)
    generated_at: datetime


class OpsReportGenerateResponse(BaseModel):
    filename: str
    stored: bool
    content_type: str = "application/pdf"
    period: OpsPeriod
    generated_at: datetime
