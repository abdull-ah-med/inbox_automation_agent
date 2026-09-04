"""Promotion proposals API — admin review queue for scope widenings."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import SettingsDep, get_db
from app.core.dependencies_auth import CurrentAdmin
from app.core.rate_limit import limiter
from app.repositories import promotion_proposal_repo
from app.repositories.promotion_proposal_repo import PromotionProposalSchema
from app.services import promotion_service
from app.services.promotion_service import HighChangeRateError

router = APIRouter(prefix="/api/promotion-proposals", tags=["promotion-proposals"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _require_proposal(
    session: AsyncSession,
    settings: SettingsDep,
    proposal_id: uuid.UUID,
):
    proposal = await promotion_proposal_repo.get_proposal_by_id(session, proposal_id)
    if proposal is None or not settings.mailbox_allowed(proposal.mailbox):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found")
    return proposal


@router.get(
    "",
    response_model=list[PromotionProposalSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def list_promotion_proposals(
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
    mailbox: Annotated[str | None, Query(max_length=320)] = None,
    kind: Annotated[str | None, Query(max_length=40)] = None,
    proposal_status: Annotated[str | None, Query(alias="status", max_length=20)] = "pending",
) -> list[PromotionProposalSchema]:
    _ = request, response
    if mailbox is not None and not settings.mailbox_allowed(mailbox):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found")
    return await promotion_proposal_repo.list_proposals(
        session,
        mailbox=mailbox,
        kind=kind,
        status=proposal_status,
        limit=200,
    )


@router.post(
    "/{proposal_id}/accept",
    response_model=PromotionProposalSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def accept_proposal(
    proposal_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
    confirm_high_change_rate: Annotated[bool, Query()] = False,
) -> PromotionProposalSchema:
    """Accept via promotion_service (golden-set gate + canary rule create)."""
    _ = request, response
    await _require_proposal(session, settings, proposal_id)
    try:
        updated = await promotion_service.accept_proposal(
            session,
            proposal_id,
            actor=_admin.email,
            confirm_high_change_rate=confirm_high_change_rate,
        )
    except HighChangeRateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "high_change_rate",
                "change_rate": exc.change_rate,
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    await session.commit()
    return updated


@router.post(
    "/{proposal_id}/dismiss",
    response_model=PromotionProposalSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def dismiss_proposal(
    proposal_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
) -> PromotionProposalSchema:
    _ = request, response
    await _require_proposal(session, settings, proposal_id)
    try:
        updated = await promotion_service.dismiss_proposal(
            session,
            proposal_id,
            actor=_admin.email,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    await session.commit()
    return updated


@router.post(
    "/{proposal_id}/revert",
    response_model=PromotionProposalSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def revert_proposal(
    proposal_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    _admin: CurrentAdmin,
) -> PromotionProposalSchema:
    """Revert via promotion_service (archive rule + invalidate chat cache)."""
    _ = request, response
    await _require_proposal(session, settings, proposal_id)
    try:
        updated = await promotion_service.revert_promotion(
            session,
            proposal_id,
            actor=_admin.email,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    await session.commit()
    return updated
