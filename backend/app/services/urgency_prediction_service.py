"""Urgency prediction service — log probability vectors at triage/draft time."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import urgency_prediction_repo
from app.repositories.urgency_prediction_repo import UrgencyPredictionSchema

logger = structlog.get_logger(__name__)

_DEFAULT_PROBS: dict[str, float] = {
    "CRITICAL": 0.0,
    "HIGH": 0.0,
    "NORMAL": 1.0,
    "LOW": 0.0,
}


def _normalize_probs(probs: dict[str, float] | None, urgency: str) -> dict[str, float]:
    """Return a well-formed {CRITICAL,HIGH,NORMAL,LOW} probability dict.

    If the caller supplies probs, pass them through (add missing keys as 0.0).
    Otherwise build a one-hot dict from the urgency label alone.
    """
    levels = ("CRITICAL", "HIGH", "NORMAL", "LOW")
    if probs:
        base = {k: max(0.0, float(probs.get(k, 0.0))) for k in levels}
        total = sum(base.values())
        if total > 0:
            return {k: v / total for k, v in base.items()}
        base = dict.fromkeys(levels, 0.0)
        if urgency in levels:
            base[urgency] = 1.0
        return base
    base = dict.fromkeys(levels, 0.0)
    if urgency in levels:
        base[urgency] = 1.0
    return base


async def log_prediction(
    session: AsyncSession,
    *,
    draft_id: uuid.UUID,
    thread_id: uuid.UUID,
    mailbox: str,
    sender_domain: str,
    predicted_urgency: str,
    final_urgency: str,
    probs: dict[str, float] | None = None,
    routing_category: str | None = None,
    alert_fingerprint: str | None = None,
    applied_rule_ids: list[uuid.UUID] | None = None,
) -> UrgencyPredictionSchema | None:
    """Persist a triage urgency prediction row. Best-effort — never raises."""
    try:
        normalized = _normalize_probs(probs, predicted_urgency)
        result = await urgency_prediction_repo.insert_urgency_prediction(
            session,
            draft_id=draft_id,
            thread_id=thread_id,
            mailbox=mailbox,
            routing_category=routing_category,
            sender_domain=sender_domain,
            predicted_urgency=predicted_urgency,
            probs=normalized,
            alert_fingerprint=alert_fingerprint,
            applied_rule_ids=applied_rule_ids,
            final_urgency=final_urgency,
        )
        logger.info(
            "urgency_prediction_logged",
            draft_id=str(draft_id),
            mailbox=mailbox,
            predicted_urgency=predicted_urgency,
            final_urgency=final_urgency,
        )
        return result
    except Exception:
        logger.warning(
            "urgency_prediction_log_failed",
            draft_id=str(draft_id),
            mailbox=mailbox,
        )
        return None
