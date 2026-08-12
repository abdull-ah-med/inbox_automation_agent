"""Ops metrics preview and weekly report download."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import limiter
from app.models.schemas.ops_report import OpsMetricsResponse, OpsReportGenerateResponse
from app.services import ops_metrics_service
from app.services.ops_report_job import generate_ops_report
from app.services.ops_report_renderer import CONTENT_TYPE

router = APIRouter(prefix="/api/reports", tags=["reports"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def _window(
    date_from: datetime | None,
    date_to: datetime | None,
    timezone_name: str,
) -> tuple[datetime, datetime]:
    return ops_metrics_service.resolve_query_window(date_from, date_to, timezone_name)


@router.get(
    "/ops-metrics",
    response_model=OpsMetricsResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def ops_metrics(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    mailbox: Annotated[str | None, Query()] = None,
) -> OpsMetricsResponse:
    start, end = _window(date_from, date_to, settings.ops_report_timezone)
    return await ops_metrics_service.get_metrics(
        session,
        settings,
        start,
        end,
        mailbox=mailbox,
    )


@router.get(
    "/ops-weekly",
    status_code=status.HTTP_200_OK,
    response_model=None,
)
@limiter.limit("20/minute")
async def download_ops_weekly(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    mailbox: Annotated[str | None, Query()] = None,
) -> Response:
    start, end = _window(date_from, date_to, settings.ops_report_timezone)
    pdf_bytes, result = await generate_ops_report(
        session,
        settings,
        start,
        end,
        mailbox=mailbox,
        persist=False,
        send_email=False,
    )
    quoted = quote(result.filename)
    disposition = f"attachment; filename=\"{result.filename}\"; filename*=UTF-8''{quoted}"
    return Response(
        content=pdf_bytes,
        media_type=CONTENT_TYPE,
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "no-store",
        },
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/ops-weekly/generate",
    response_model=OpsReportGenerateResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("20/minute")
async def generate_ops_weekly(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    mailbox: Annotated[str | None, Query()] = None,
) -> OpsReportGenerateResponse:
    start, end = _window(date_from, date_to, settings.ops_report_timezone)
    _pdf, result = await generate_ops_report(
        session,
        settings,
        start,
        end,
        mailbox=mailbox,
        persist=True,
        send_email=False,
    )
    return result
