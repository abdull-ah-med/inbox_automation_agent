"""Human-readable activity entries derived from audit events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class NarrativeEntry:
    title: str
    body: str
    actor_kind: str
    event_type: str
    timestamp: datetime


_FALLBACK_TITLES: dict[str, str] = {
    "thread.resolved.draftassistant": "Resolved by DraftAssistant",
    "thread.resolved.sent_reply_detected": "Resolved from sent reply",
    "thread.resolved.closing_mail": "Resolved from closing mail",
    "thread.resolved.reviewer": "Marked resolved",
    "thread.outcome.closing_inbound": "Closed — courtesy inbound",
    "thread.urgency.recurrence_escalated": "Urgency raised for recurring alert",
    "thread.urgency.recurrence_wrong": "Automatic urgency bump marked wrong",
    "thread.reopened.resolution_feedback": "Reopened after feedback",
    "thread.reopened.inbound_followup": "Reopened after new inbound",
    "thread.resolved.wrong_reason": "Resolution reason corrected",
}


_RESOLVE_MODES: dict[str, Literal["auto", "manual"]] = {
    "thread.resolved.reviewer": "manual",
    "thread.resolved.sent_reply_detected": "auto",
    "thread.resolved.closing_mail": "auto",
    "thread.resolved.draftassistant": "auto",
    "thread.outcome.closing_inbound": "auto",
    "thread.outcome.no_action": "auto",
}
_REOPEN_EVENTS = frozenset(
    {
        "thread.reopened.resolution_feedback",
        "thread.reopened.inbound_followup",
    }
)


def resolution_mode_from_events(
    events: list[Mapping[str, Any]],
) -> Literal["auto", "manual"] | None:
    """Latest resolution provenance after any reopen clears prior auto/manual state."""
    mode: Literal["auto", "manual"] | None = None
    for row in events:
        event_type = str(row.get("event_type") or row.get("event") or "")
        if event_type in _REOPEN_EVENTS:
            mode = None
        elif event_type in _RESOLVE_MODES:
            mode = _RESOLVE_MODES[event_type]
    return mode


def provenance_from_events(events: list[Mapping[str, Any]]) -> dict[str, str | None]:
    """Latest resolve/reopen snapshot fields for presentation."""
    out: dict[str, str | None] = {
        "resolved_by": None,
        "resolution_reason": None,
        "resolution_summary": None,
        "forced_disposition": None,
    }
    for row in events:
        event_type = str(row.get("event_type") or row.get("event") or "")
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        if event_type in _REOPEN_EVENTS:
            restore = payload.get("restore_disposition")
            out = {
                "resolved_by": None,
                "resolution_reason": None,
                "resolution_summary": None,
                "forced_disposition": str(restore) if restore else None,
            }
            continue
        if event_type not in _RESOLVE_MODES:
            continue
        snapshot = (
            payload.get("resolve_snapshot")
            if isinstance(payload.get("resolve_snapshot"), Mapping)
            else {}
        )
        resolved_by = "elise" if event_type == "thread.resolved.reviewer" else "draftassistant"
        if isinstance(snapshot.get("resolved_by"), str) and snapshot["resolved_by"]:
            resolved_by = str(snapshot["resolved_by"])
        reason = snapshot.get("resolution_reason") or payload.get("resolution_reason")
        summary = snapshot.get("resolution_summary") or payload.get("resolution_summary")
        out = {
            "resolved_by": resolved_by,
            "resolution_reason": str(reason) if reason else None,
            "resolution_summary": str(summary) if summary else None,
            "forced_disposition": None,
        }
    return out


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
