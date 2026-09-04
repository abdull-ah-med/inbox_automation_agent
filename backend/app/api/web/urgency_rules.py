"""Urgency rules API — list and lifecycle transitions (pause / resume / archive)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import SettingsDep, get_db
from app.core.dependencies_auth import CurrentAdmin
from app.core.rate_limit import limiter
from app.repositories import urgency_rule_repo
from app.repositories.urgency_rule_repo import UrgencyRuleSchema

router = APIRouter(prefix="/api/urgency-rules", tags=["urgency-rules"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _require_allowed_rule(
    session: AsyncSession,
    settings: SettingsDep,
    rule_id: uuid.UUID,
) -> UrgencyRuleSchema:
    rule = await urgency_rule_repo.get_urgency_rule_by_id(session, rule_id)
    if rule is None or not settings.mailbox_allowed(rule.mailbox):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Urgency rule not found",
        )
    return rule


@router.get(
    "",
    response_model=list[UrgencyRuleSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def list_urgency_rules(
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
    mailbox: Annotated[str, Query(min_length=1, max_length=320)],
    rule_status: Annotated[str | None, Query(alias="status", max_length=20)] = None,
) -> list[UrgencyRuleSchema]:
    _ = request, response
    if not settings.mailbox_allowed(mailbox):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found")
    return await urgency_rule_repo.list_urgency_rules_by_mailbox_status(
        session,
        mailbox=mailbox,
        status=rule_status,
        limit=200,
    )


@router.post(
    "/{rule_id}/pause",
    response_model=UrgencyRuleSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def pause_urgency_rule(
    rule_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
) -> UrgencyRuleSchema:
    _ = request, response
    from app.services.urgency_rule_service import pause_rule

    await _require_allowed_rule(session, settings, rule_id)
    updated = await pause_rule(session, rule_id)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Urgency rule not found",
        )
    await session.commit()
    return updated


@router.post(
    "/{rule_id}/resume",
    response_model=UrgencyRuleSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def resume_urgency_rule(
    rule_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
) -> UrgencyRuleSchema:
    _ = request, response
    from app.services.urgency_rule_service import resume_rule

    await _require_allowed_rule(session, settings, rule_id)
    updated = await resume_rule(session, rule_id)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Urgency rule not found",
        )
    await session.commit()
    return updated


@router.post(
    "/{rule_id}/archive",
    response_model=UrgencyRuleSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def archive_urgency_rule(
    rule_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
) -> UrgencyRuleSchema:
    _ = request, response
    await _require_allowed_rule(session, settings, rule_id)
    return await _set_rule_status(session, rule_id, "archived")


async def _set_rule_status(
    session: AsyncSession,
    rule_id: uuid.UUID,
    new_status: str,
) -> UrgencyRuleSchema:
    updated = await urgency_rule_repo.set_urgency_rule_status(session, rule_id, new_status)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Urgency rule not found",
        )
    await session.commit()
    return updated
