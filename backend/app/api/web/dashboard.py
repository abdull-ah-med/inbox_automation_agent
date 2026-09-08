"""Dashboard overview routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import api_default_limit_value, limiter
from app.models.schemas.dashboard import DashboardOverview
from app.services import dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.get(
    "/overview",
    response_model=DashboardOverview,
    status_code=status.HTTP_200_OK,
)
@limiter.limit(api_default_limit_value)
async def overview(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
    needs_attention_sort: Annotated[
        Literal["urgency", "recent"],
        Query(description="Needs Attention queue sort: urgency or recent"),
    ] = "urgency",
) -> DashboardOverview:
    _ = request, response
    return await dashboard_service.get_overview(
        session,
        settings,
        needs_attention_sort=needs_attention_sort,
    )
