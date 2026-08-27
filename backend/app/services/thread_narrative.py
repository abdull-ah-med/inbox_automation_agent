"""Human-readable activity entries derived from audit events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class NarrativeEntry:
    title: str
    body: str
    actor_kind: str
    event_type: str
    timestamp: datetime


_FALLBACK_TITLES: dict[str, str] = {
    "thread.resolved.sent_reply_detected": "Resolved from sent reply",
    "thread.resolved.closing_mail": "Resolved from closing mail",
    "thread.resolved.reviewer": "Marked resolved",
    "thread.outcome.closing_inbound": "Closed — courtesy inbound",
    "thread.urgency.recurrence_escalated": "Urgency raised for recurring alert",
    "thread.urgency.recurrence_wrong": "Automatic urgency bump marked wrong",
    "thread.reopened.resolution_feedback": "Reopened after feedback",
    "thread.resolved.wrong_reason": "Resolution reason corrected",
}


def _fallback_body(event_type: str, payload: Mapping[str, Any]) -> str:
    if event_type == "thread.resolved.sent_reply_detected":
        matched = str(payload.get("matched_by") or "sent reply")
        urgency = payload.get("urgency_assessed")
        matched_label = matched.replace("_", " ")
        base = f"Matched your Outlook send ({matched_label}). Removed from Needs Attention."
        if urgency:
            return f"{base} Assessed urgency was {urgency}; it no longer drives priority."
        return base
    if event_type == "thread.urgency.recurrence_escalated":
        floor = payload.get("floor") or "HIGH"
        count = payload.get("count_48h")
        if count is not None:
            return (
                f"Urgency bumped automatically: {count} similar alerts in 48h "
                f"(same sender and subject). Urgency raised to {floor}."
            )
        return f"Recurring automated alert. Urgency raised to {floor}."
    if event_type == "thread.urgency.recurrence_wrong":
        return "Reverted this thread. This alert fingerprint will not auto-bump again."
    return _FALLBACK_TITLES.get(event_type, event_type.replace(".", " ").title())


def build_activity(events: list[Mapping[str, Any]]) -> list[NarrativeEntry]:
    """Map audit-like dicts to Elise-facing timeline entries (newest last ok)."""
    out: list[NarrativeEntry] = []
    for row in events:
        event_type = str(row.get("event_type") or row.get("event") or "")
        payload = row.get("payload") or {}
        if not isinstance(payload, Mapping):
            payload = {}
        human = payload.get("human") if isinstance(payload.get("human"), Mapping) else None
        created = row.get("created_at") or row.get("timestamp")
        if not isinstance(created, datetime):
            continue
        if human:
            title = str(human.get("title") or _FALLBACK_TITLES.get(event_type, event_type))
            body = str(human.get("body") or "")
            actor = str(human.get("actor_kind") or "agent")
        else:
            title = _FALLBACK_TITLES.get(event_type, event_type.replace(".", " ").title())
            body = _fallback_body(event_type, payload)
            actor = "agent"
        out.append(
            NarrativeEntry(
                title=title,
                body=body,
                actor_kind=actor,
                event_type=event_type,
                timestamp=created,
            )
        )
    return out
