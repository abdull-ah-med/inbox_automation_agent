"""Promotion proposal repository — admin card queue for proposed scope widenings."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.promotion_proposal import PromotionProposal


class PromotionProposalSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mailbox: str
    kind: str
    payload: dict[str, Any]
    impact_num: int
    impact_den: int
    precision_num: int | None = None
    precision_den: int | None = None
    evidence_ids: list[uuid.UUID]
    status: str
    expires_at: datetime
    created_at: datetime | None = None
    dedupe_key: str | None = None


def compute_dedupe_key(*, mailbox: str, kind: str, payload: dict[str, Any]) -> str:
    """Stable sha256 of mailbox + kind + condition/from_scope/domain/action."""
    condition = payload.get("condition") if isinstance(payload.get("condition"), dict) else {}
    action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
    canonical = {
        "mailbox": mailbox.strip().lower(),
        "kind": kind,
        "condition": condition.get("sender_domain") if condition else None,
        "from_scope": payload.get("from_scope"),
        "domain": payload.get("sender_domain")
        or (condition.get("sender_domain") if condition else None),
        "action": action.get("set_urgency_floor") or action.get("set_urgency"),
        "note_id": str(payload["note_id"]) if payload.get("note_id") else None,
        "requested_scope": payload.get("requested_scope"),
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def create_promotion_proposal(
    session: AsyncSession,
    *,
    mailbox: str,
    kind: str,
    payload: dict[str, Any],
    impact_num: int,
    impact_den: int,
    evidence_ids: list[uuid.UUID],
    expires_at: datetime,
    precision_num: int | None = None,
    precision_den: int | None = None,
) -> PromotionProposalSchema:
    dedupe_key = compute_dedupe_key(mailbox=mailbox, kind=kind, payload=payload)
    stmt = (
        pg_insert(PromotionProposal)
        .values(
            mailbox=mailbox,
            kind=kind,
            payload=payload,
            impact_num=impact_num,
            impact_den=impact_den,
            evidence_ids=evidence_ids,
            expires_at=expires_at,
            precision_num=precision_num,
            precision_den=precision_den,
            dedupe_key=dedupe_key,
        )
        .on_conflict_do_nothing(
            index_elements=["mailbox", "kind", "dedupe_key"],
            index_where=text("status = 'pending'"),
        )
        .returning(PromotionProposal)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        await session.flush()
        return PromotionProposalSchema.model_validate(row)

    existing = (
        await session.execute(
            select(PromotionProposal).where(
                PromotionProposal.mailbox == mailbox,
                PromotionProposal.kind == kind,
                PromotionProposal.dedupe_key == dedupe_key,
                PromotionProposal.status == "pending",
            )
        )
    ).scalar_one()
    return PromotionProposalSchema.model_validate(existing)


async def list_pending_proposals(
    session: AsyncSession,
    *,
    mailbox: str,
    limit: int = 100,
) -> list[PromotionProposalSchema]:
    stmt = (
        select(PromotionProposal)
        .where(
            PromotionProposal.mailbox == mailbox,
            PromotionProposal.status == "pending",
        )
        .order_by(PromotionProposal.created_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    result = await session.execute(stmt)
    return [PromotionProposalSchema.model_validate(row) for row in result.scalars().all()]


async def set_proposal_status(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    status: str,
    payload: dict[str, Any] | None = None,
    expected_from: str | None = None,
) -> PromotionProposalSchema | None:
    values: dict[str, Any] = {"status": status}
    if payload is not None:
        values["payload"] = payload
    stmt = sa_update(PromotionProposal).where(PromotionProposal.id == proposal_id)
    if expected_from is not None:
        stmt = stmt.where(PromotionProposal.status == expected_from)
    stmt = stmt.values(**values).returning(PromotionProposal)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return PromotionProposalSchema.model_validate(row)


async def get_proposal_by_id(
    session: AsyncSession,
    proposal_id: uuid.UUID,
) -> PromotionProposalSchema | None:
    stmt = select(PromotionProposal).where(PromotionProposal.id == proposal_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return PromotionProposalSchema.model_validate(row)


async def list_proposals(
    session: AsyncSession,
    *,
    mailbox: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[PromotionProposalSchema]:
    stmt = (
        select(PromotionProposal)
        .order_by(PromotionProposal.created_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    if mailbox is not None:
        stmt = stmt.where(PromotionProposal.mailbox == mailbox)
    if kind is not None:
        stmt = stmt.where(PromotionProposal.kind == kind)
    if status is not None:
        stmt = stmt.where(PromotionProposal.status == status)
    result = await session.execute(stmt)
    return [PromotionProposalSchema.model_validate(row) for row in result.scalars().all()]
