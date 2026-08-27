"""Dashboard overview routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import CurrentUser
from app.core.rate_limit import limiter
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
@limiter.limit("120/minute")
async def overview(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    _user: CurrentUser,
) -> DashboardOverview:
    _ = request, response
    return await dashboard_service.get_overview(session, settings)
