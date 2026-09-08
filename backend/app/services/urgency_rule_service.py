"""Urgency rule service — post-model LF evaluation (Snorkel/PagerDuty pattern).

Applies active and canary urgency rules to adjust the model's predicted urgency.
Also clusters urgency edits into promotion proposals (propose_from_edits).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.db.thread import Thread
from app.models.db.urgency_feedback import UrgencyFeedback
from app.models.db.urgency_prediction import UrgencyPrediction
from app.repositories import promotion_proposal_repo, urgency_prediction_repo, urgency_rule_repo
from app.repositories.promotion_proposal_repo import PromotionProposalSchema
from app.repositories.urgency_rule_repo import UrgencyRuleSchema
from app.workers.canary_sweeper_worker import canary_override_too_high

logger = structlog.get_logger(__name__)

_URGENCY_ORDER = {"LOW": 0, "NORMAL": 1, "HIGH": 2, "CRITICAL": 3}
_URGENCY_LABELS = frozenset({"LOW", "NORMAL", "HIGH", "CRITICAL"})
_COSINE_CLUSTER_THRESHOLD = 0.85
_PROPOSAL_MIN_CLUSTER = 3
_PROPOSAL_EXPIRY_DAYS = 30
_PROPOSAL_LOOKBACK_DAYS = 90
_PROPOSAL_EDIT_CAP = 2000


def sanitize_rule_condition(condition: object) -> dict:
    """Keep only well-typed condition keys. Drop poisoned body_contains_any entries."""
    if not isinstance(condition, dict):
        return {}
    cleaned: dict = {}
    domain = condition.get("sender_domain")
    if isinstance(domain, str) and domain.strip():
        cleaned["sender_domain"] = domain.strip().lower()
    keywords = condition.get("body_contains_any")
    if isinstance(keywords, list):
        kept = [kw.strip() for kw in keywords if isinstance(kw, str) and kw.strip()]
        if kept:
            cleaned["body_contains_any"] = kept
    if condition.get("always") is True:
        cleaned["always"] = True
    return cleaned


def sanitize_rule_action(action: object) -> dict:
    """Keep only known urgency labels for set_urgency / set_urgency_floor."""
    if not isinstance(action, dict):
        return {}
    cleaned: dict = {}
    for key in ("set_urgency", "set_urgency_floor"):
        raw = action.get(key)
        if raw is None:
            continue
        label = str(raw).strip().upper()
        if label in _URGENCY_LABELS:
            cleaned[key] = label
    return cleaned


@dataclass(frozen=True, slots=True)
class UrgencyDecision:
    final_urgency: str
    applied_rule_ids: list[uuid.UUID] = field(default_factory=list)


def _matches_condition(
    rule: UrgencyRuleSchema,
    *,
    sender_domain: str,
    body_text: str,
) -> bool:
    """Return True when the rule's condition DSL matches the email signals."""
    cond = rule.condition
    if not cond:
        return False
    # sender_domain equality check
    cond_domain = cond.get("sender_domain")
    if cond_domain is not None:
        if not isinstance(cond_domain, str) or not isinstance(sender_domain, str):
            return False
        if cond_domain.lower() != sender_domain.lower():
            return False
    # optional body keyword check (any match is sufficient)
    raw_keywords = cond.get("body_contains_any")
    if raw_keywords is not None:
        if not isinstance(raw_keywords, list):
            return False
        keywords = [kw for kw in raw_keywords if isinstance(kw, str) and kw]
        if not keywords:
            return False
        body_lower = body_text.lower() if isinstance(body_text, str) else ""
        if not any(kw.lower() in body_lower for kw in keywords):
            return False
    # always-true shortcut (used in tests and internal rules)
    return not ("always" in cond and not cond["always"])


def _apply_action(current: str, action: dict) -> str:
    """Apply a rule action and return the new urgency label.

    ``set_urgency`` is a hard override. ``set_urgency_floor`` is a true floor:
    it never lowers urgency.
    """
    if "set_urgency" in action and action["set_urgency"] is not None:
        return str(action["set_urgency"])
    floor = action.get("set_urgency_floor")
    if floor is not None:
        current_rank = _URGENCY_ORDER.get(current, 1)
        floor_rank = _URGENCY_ORDER.get(str(floor), 1)
        if floor_rank > current_rank:
            return str(floor)
        return current
    return current


async def apply_after_model(
    session: AsyncSession,
    *,
    mailbox: str,
    predicted_urgency: str,
    sender_domain: str,
    alert_fingerprint: str | None,
    body_text: str,
    prediction_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> UrgencyDecision:
    """Evaluate active + canary rules and return the adjusted urgency.

    Never raises. On any failure, returns the model's predicted urgency unchanged.
    When *settings* is provided and ``urgency_rules_enabled`` is False, skip rules.
    """
    _ = alert_fingerprint
    _ = prediction_id
    if settings is not None and not settings.urgency_rules_enabled:
        return UrgencyDecision(final_urgency=predicted_urgency)
    if not isinstance(sender_domain, str):
        sender_domain = ""
    try:
        rules = await urgency_rule_repo.list_live_urgency_rules(session, mailbox=mailbox)
        final = predicted_urgency
        applied: list[uuid.UUID] = []
        for rule in rules:
            if _matches_condition(rule, sender_domain=sender_domain, body_text=body_text):
                final = _apply_action(final, rule.action)
                applied.append(rule.id)
                logger.info(
                    "urgency_rule_fired",
                    rule_id=str(rule.id),
                    mailbox=mailbox,
                    predicted=predicted_urgency,
                    final=final,
                    scope=rule.scope,
                    scope_key=rule.scope_key,
                )
        if applied:
            try:
                await urgency_rule_repo.increment_hit_counts(session, applied)
            except Exception:
                logger.warning(
                    "urgency_rule_hit_increment_failed", rule_ids=[str(i) for i in applied]
                )
        return UrgencyDecision(final_urgency=final, applied_rule_ids=applied)
    except Exception:
        logger.warning("urgency_rule_apply_failed", mailbox=mailbox)
        return UrgencyDecision(final_urgency=predicted_urgency)


async def pause_rule(session: AsyncSession, rule_id: uuid.UUID) -> UrgencyRuleSchema | None:
    """Pause a rule, recording paused_at. Does not clear canary_until."""
    rule = await urgency_rule_repo.get_urgency_rule_by_id(session, rule_id)
    if rule is None:
        return None
    extra: dict = {"paused_at": datetime.now(UTC), "previous_status": rule.status}
    return await urgency_rule_repo.set_urgency_rule_status(session, rule_id, "paused", extra=extra)


async def resume_rule(session: AsyncSession, rule_id: uuid.UUID) -> UrgencyRuleSchema | None:
    """Resume a paused rule: restore previous_status, never promoting a live canary."""
    rule = await urgency_rule_repo.get_urgency_rule_by_id(session, rule_id)
    if rule is None:
        return None
    now = datetime.now(UTC)
    until = rule.canary_until
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    previous = getattr(rule, "previous_status", None)
    if previous == "active":
        new_status = "active"
    elif until is not None and until > now:
        new_status = "canary"
    elif canary_override_too_high(rule.hit_count or 0, rule.override_count or 0):
        new_status = "archived"
    else:
        new_status = "active"
    return await urgency_rule_repo.set_urgency_rule_status(session, rule_id, new_status)


async def record_overrides_if_edited(
    session: AsyncSession,
    *,
    draft_id: uuid.UUID,
    previous_urgency: str,
    new_urgency: str,
) -> None:
    """Increment override_count on rules that produced *previous_urgency*."""
    if previous_urgency == new_urgency:
        return

    pred = await urgency_prediction_repo.get_urgency_prediction_by_draft_id(session, draft_id)
    if pred is None or not pred.applied_rule_ids:
        await urgency_prediction_repo.set_elise_edited_to(session, draft_id, new_urgency)
        return
    if pred.final_urgency != previous_urgency:
        await urgency_prediction_repo.set_elise_edited_to(session, draft_id, new_urgency)
        return
    for rule_id in pred.applied_rule_ids:
        try:
            await urgency_rule_repo.increment_override_count(session, rule_id)
        except Exception:
            logger.warning("urgency_rule_override_increment_failed", rule_id=str(rule_id))
    await urgency_prediction_repo.set_elise_edited_to(session, draft_id, new_urgency)


def _embedding_nonzero(vec: object) -> bool:
    if vec is None:
        return False
    try:
        return any(abs(float(x)) > 1e-12 for x in vec)
    except TypeError:
        return False


def _cosine_sim(a: object, b: object) -> float:
    try:
        left = [float(x) for x in a]  # type: ignore[arg-type]
        right = [float(x) for x in b]  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(x * y for x, y in zip(left, right, strict=False))
    na = math.sqrt(sum(x * x for x in left))
    nb = math.sqrt(sum(y * y for y in right))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _direction(prev: str, nxt: str) -> str:
    return "down" if _URGENCY_ORDER.get(nxt, 1) < _URGENCY_ORDER.get(prev, 1) else "up"


def _union_find_clusters(indices: list[int], similar) -> list[list[int]]:
    parent = {i: i for i in indices}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(indices):
        for b in indices[i + 1 :]:
            if similar(a, b):
                union(a, b)
    groups: dict[int, list[int]] = {}
    for i in indices:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


async def _backfill_precision(
    session: AsyncSession,
    *,
    mailbox: str,
    domain: str,
    new_urgency: str,
) -> tuple[int | None, int | None]:
    traces = await urgency_prediction_repo.list_recent_for_mailbox(session, mailbox, limit=200)
    num = 0
    den = 0
    for pred in traces:
        if (pred.sender_domain or "").lower() != domain.lower():
            continue
        if not pred.elise_edited_to:
            continue
        den += 1
        if pred.elise_edited_to == new_urgency:
            num += 1
    if den == 0:
        return None, None
    return num, den


def _domain_from_sender_norm(sender_norm: str | None) -> str:
    if not sender_norm:
        return ""
    value = sender_norm.strip().lower()
    if "@" in value:
        return value.rsplit("@", 1)[-1]
    return value


async def propose_from_edits(
    session: AsyncSession,
    mailbox: str,
) -> list[PromotionProposalSchema]:
    """Cluster urgency edits by sender_domain + direction; propose rules when >= 3.

    When reason embeddings are non-zero, cluster by cosine >= 0.85 within the
    same domain and direction. Zero embeddings fall back to (domain, prev, next).
    """
    stmt = (
        select(
            UrgencyFeedback.id,
            UrgencyFeedback.previous_urgency,
            UrgencyFeedback.new_urgency,
            UrgencyFeedback.embedding,
            UrgencyPrediction.sender_domain,
            Thread.alert_sender_norm,
        )
        .select_from(UrgencyFeedback)
        .join(Thread, UrgencyFeedback.thread_id == Thread.id)
        .outerjoin(UrgencyPrediction, UrgencyFeedback.draft_id == UrgencyPrediction.draft_id)
        .where(
            UrgencyFeedback.mailbox == mailbox,
            UrgencyFeedback.is_excluded.is_(False),
            UrgencyFeedback.previous_urgency.is_not(None),
            UrgencyFeedback.created_at
            >= datetime.now(UTC) - timedelta(days=_PROPOSAL_LOOKBACK_DAYS),
        )
        .order_by(UrgencyFeedback.created_at.desc())
        .limit(_PROPOSAL_EDIT_CAP)
    )
    rows = (await session.execute(stmt)).all()

    parsed: list[tuple[uuid.UUID, str, str, object, str]] = []
    for fb_id, prev, nxt, embedding, pred_domain, sender_norm in rows:
        if not prev or not nxt or prev == nxt:
            continue
        domain = pred_domain or _domain_from_sender_norm(sender_norm)
        if not domain:
            continue
        parsed.append((fb_id, prev, nxt, embedding, domain.lower()))

    cluster_members: list[tuple[str, str, str, list[uuid.UUID]]] = []

    fallback: dict[tuple[str, str, str], list[uuid.UUID]] = {}
    embeddable: dict[tuple[str, str], list[int]] = {}
    for idx, (fb_id, prev, nxt, embedding, domain) in enumerate(parsed):
        if _embedding_nonzero(embedding):
            embeddable.setdefault((domain, _direction(prev, nxt)), []).append(idx)
        else:
            fallback.setdefault((domain, prev, nxt), []).append(fb_id)

    for (domain, prev, nxt), ids in fallback.items():
        cluster_members.append((domain, prev, nxt, ids))

    for (domain, _), indices in embeddable.items():
        groups = _union_find_clusters(
            indices,
            lambda a, b: _cosine_sim(parsed[a][3], parsed[b][3]) >= _COSINE_CLUSTER_THRESHOLD,
        )
        for group in groups:
            members = [parsed[i] for i in group]
            counts: dict[tuple[str, str], int] = {}
            for _id, prev, nxt, _emb, _dom in members:
                counts[(prev, nxt)] = counts.get((prev, nxt), 0) + 1
            (prev, nxt) = max(counts, key=counts.get)  # type: ignore[arg-type]
            cluster_members.append((domain, prev, nxt, [m[0] for m in members]))

    proposals: list[PromotionProposalSchema] = []
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=_PROPOSAL_EXPIRY_DAYS)

    for domain, prev_urgency, new_urgency, evidence_ids in cluster_members:
        count = len(evidence_ids)
        if count < _PROPOSAL_MIN_CLUSTER:
            continue

        direction = _direction(prev_urgency, new_urgency)
        payload = {
            "condition": {"sender_domain": domain},
            "action": {"set_urgency": new_urgency},
            "direction": direction,
            "from_urgency": prev_urgency,
            "person_bound": False,
            "description": (
                f"The last {count} edits from @{domain} changed urgency "
                f"{prev_urgency} → {new_urgency}."
            ),
        }
        precision_num, precision_den = await _backfill_precision(
            session, mailbox=mailbox, domain=domain, new_urgency=new_urgency
        )

        try:
            proposal = await promotion_proposal_repo.create_promotion_proposal(
                session,
                mailbox=mailbox,
                kind="urgency_rule",
                payload=payload,
                impact_num=count,
                impact_den=count,
                evidence_ids=list(evidence_ids),
                expires_at=expires_at,
                precision_num=precision_num,
                precision_den=precision_den,
            )
            proposals.append(proposal)
            logger.info(
                "urgency_rule_proposal_created",
                mailbox=mailbox,
                sender_domain=domain,
                direction=direction,
                count=count,
            )
        except Exception:
            logger.warning(
                "urgency_rule_proposal_failed",
                mailbox=mailbox,
                sender_domain=domain,
            )

    return proposals
