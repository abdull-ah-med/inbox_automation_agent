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


_TITLES: dict[str, str] = {
    "draft.generated": "Draft ready",
    "draft.regenerated": "Draft updated",
    "draft.requires_human": "Needs a human look",
    "draft.approved": "You approved a draft",
    "draft.rejected": "You rejected a draft",
    "draft.marked_wrong": "Draft marked wrong",
    "draft.skipped_already_replied": "Skipped — already replied",
    "draft.skill_reference_read": "Playbook opened",
    "skills.selected": "Playbooks for this draft",
    "context.match": "Related thread found",
    "context.no_match": "No related thread",
    "triage.action_needed": "Needs attention",
    "triage.spam_discarded": "Marked as junk",
    "triage.no_action_discarded": "No action needed",
    "triage.failed": "Triage failed",
    "slack.card_posted": "Posted to Slack",
    "thread.resolved.draftassistant": "DraftAssistant closed this",
    "thread.resolved.sent_reply_detected": "Resolved from sent reply",
    "thread.resolved.closing_mail": "Closed from a closing email",
    "thread.resolved.reviewer": "You resolved this",
    "thread.outcome.closing_inbound": "Closed — courtesy reply",
    "thread.outcome.no_action": "Closed — no action",
    "thread.urgency.recurrence_escalated": "Urgency raised for recurring alert",
    "thread.urgency.recurrence_wrong": "Automatic urgency bump marked wrong",
    "thread.reopened.resolution_feedback": "Reopened",
    "thread.reopened.inbound_followup": "Reopened",
    "thread.resolved.wrong_reason": "Close reason corrected",
    "thread.urgency.edited": "Urgency changed",
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

_USER_ACTORS = frozenset(
    {
        "thread.resolved.reviewer",
        "thread.reopened.resolution_feedback",
        "thread.resolved.wrong_reason",
        "draft.approved",
        "draft.rejected",
        "draft.marked_wrong",
        "thread.urgency.edited",
        "thread.urgency.recurrence_wrong",
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


def _pretty_event(event_type: str) -> str:
    words = event_type.replace(".", " ").replace("_", " ").split()
    if not words:
        return event_type
    return " ".join(word.capitalize() for word in words)


def _title_cased_event(event_type: str) -> str:
    return event_type.replace(".", " ").title()


def _id_count(payload: Mapping[str, Any], *keys: str) -> int:
    seen: set[str] = set()
    for key in keys:
        raw = payload.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            text = str(item).strip()
            if text:
                seen.add(text)
    return len(seen)


def _actions_taken(payload: Mapping[str, Any], human_body: str) -> str | None:
    raw = payload.get("actions_taken")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    marker = "Actions taken:"
    if marker not in human_body:
        return None
    rest = human_body.split(marker, 1)[1].strip()
    if " With:" in rest:
        rest = rest.split(" With:", 1)[0].strip()
    return rest or None


def _involved(payload: Mapping[str, Any], human_body: str) -> str | None:
    raw = payload.get("involved")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    marker = " With:"
    if marker not in human_body:
        return None
    return human_body.split(marker, 1)[1].strip() or None


def _note(payload: Mapping[str, Any], human_body: str) -> str | None:
    raw = payload.get("note")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    marker = " Note: "
    if marker not in human_body:
        return None
    return human_body.split(marker, 1)[1].strip() or None


def _append_note(body: str, note: str | None) -> str:
    if not note:
        return body
    return f"{body} Note: {note}"


def _body_skills_selected(payload: Mapping[str, Any], _human_body: str) -> str:
    count = _id_count(payload, "selected_ids", "always_ids")
    if count == 0:
        return "Wrote from this thread — no extra playbook."
    if count == 1:
        return "Used 1 saved playbook for this draft."
    return f"Used {count} saved playbooks for this draft."


def _body_triage_action_needed(payload: Mapping[str, Any], _human_body: str) -> str:
    if payload.get("draft_needed") is True:
        return "This email needs a reply."
    if payload.get("has_action_items") is True:
        return "This email needs follow-up."
    return "This email needs a reply or follow-up."


def _body_sent_reply_detected(payload: Mapping[str, Any], _human_body: str) -> str:
    matched = str(payload.get("matched_by") or "")
    if matched == "approved_draft":
        return "You sent the approved draft from Outlook. Taken off Needs Attention."
    if matched == "time_window":
        return (
            "DraftAssistant saw you send from Outlook and closed this. Taken off Needs Attention."
        )
    return "You sent a reply from Outlook. Taken off Needs Attention."


def _body_resolved_reviewer(payload: Mapping[str, Any], human_body: str) -> str:
    if payload.get("already_resolved") is True:
        body = "Recorded how you resolved this thread."
    else:
        body = "You closed this and took it off Needs Attention."
    actions = _actions_taken(payload, human_body)
    involved = _involved(payload, human_body)
    if actions:
        body = f"{body} Actions taken: {actions}"
    if involved:
        body = f"{body} With: {involved}"
    return body


def _body_resolved_draftassistant(payload: Mapping[str, Any], human_body: str) -> str:
    summary = payload.get("resolution_summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    if human_body and human_body != _TITLES.get("thread.resolved.draftassistant"):
        return human_body
    return "No reply needed, so DraftAssistant closed this."


def _body_recurrence_escalated(payload: Mapping[str, Any], _human_body: str) -> str:
    floor = payload.get("floor") or "HIGH"
    count = payload.get("count_48h")
    if count is not None:
        return (
            f"{count} similar alerts in 48 hours (same sender and subject). "
            f"Urgency raised to {floor}."
        )
    return f"Recurring automated alert. Urgency raised to {floor}."


_SIMPLE_BODIES: dict[str, str] = {
    "draft.generated": "DraftAssistant wrote a reply for this thread.",
    "draft.regenerated": "DraftAssistant rewrote the reply.",
    "draft.requires_human": "DraftAssistant could not finish a draft. Check Insight for what is going on.",
    "draft.skipped_already_replied": (
        "You had already sent from Outlook, so no new draft was written."
    ),
    "context.match": "Pulled a similar past thread so the draft could stay consistent.",
    "context.no_match": "No similar past thread. Wrote from this conversation only.",
    "triage.spam_discarded": "Treated as junk and kept out of Needs Attention.",
    "triage.no_action_discarded": "No reply needed. Left out of Needs Attention.",
    "slack.card_posted": "A review card was posted to Slack.",
    "thread.resolved.closing_mail": "A closing email came in, so this is done.",
    "thread.outcome.closing_inbound": "The other side sent a courtesy close. No reply needed.",
    "thread.reopened.inbound_followup": "A new inbound email arrived, so this is open again.",
    "thread.urgency.recurrence_wrong": (
        "Reverted this thread. This alert will not auto-raise urgency again."
    ),
}

_BODY_HANDLERS: dict[str, Any] = {
    "skills.selected": _body_skills_selected,
    "triage.action_needed": _body_triage_action_needed,
    "thread.resolved.sent_reply_detected": _body_sent_reply_detected,
    "thread.resolved.reviewer": _body_resolved_reviewer,
    "thread.resolved.draftassistant": _body_resolved_draftassistant,
    "thread.reopened.resolution_feedback": lambda payload, human_body: _append_note(
        "You said this was still open. Back in Needs Attention.",
        _note(payload, human_body),
    ),
    "thread.resolved.wrong_reason": lambda payload, human_body: _append_note(
        "Recorded that DraftAssistant closed this for the wrong reason.",
        _note(payload, human_body),
    ),
    "thread.urgency.recurrence_escalated": _body_recurrence_escalated,
}


def _body_for(event_type: str, payload: Mapping[str, Any], human_body: str) -> str:
    fallback = {
        _TITLES.get(event_type, ""),
        _title_cased_event(event_type),
        _pretty_event(event_type),
    }
    if human_body and human_body not in fallback:
        return human_body
    simple = _SIMPLE_BODIES.get(event_type)
    if simple is not None:
        return simple
    handler = _BODY_HANDLERS.get(event_type)
    if handler is not None:
        return handler(payload, human_body)
    return ""


def _title_for(event_type: str, human_title: str | None) -> str:
    if human_title and human_title not in {_title_cased_event(event_type), event_type}:
        return human_title
    mapped = _TITLES.get(event_type)
    if mapped:
        return mapped
    return _pretty_event(event_type)


def _collapse_repeats(entries: list[NarrativeEntry]) -> list[NarrativeEntry]:
    if not entries:
        return []
    out = [entries[0]]
    for entry in entries[1:]:
        prev = out[-1]
        if (
            entry.event_type == prev.event_type
            and entry.title == prev.title
            and entry.body == prev.body
        ):
            out[-1] = entry
            continue
        out.append(entry)
    return out


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
        human_title = str(human.get("title") or "").strip() if human else ""
        human_body = str(human.get("body") or "").strip() if human else ""
        actor = str((human or {}).get("actor_kind") or "") if human else ""
        if not actor:
            actor = "elise" if event_type in _USER_ACTORS else "agent"
        title = _title_for(event_type, human_title or None)
        body = _body_for(event_type, payload, human_body)
        if body.strip() == title.strip():
            body = ""
        out.append(
            NarrativeEntry(
                title=title,
                body=body,
                actor_kind=actor,
                event_type=event_type,
                timestamp=created,
            )
        )
    return _collapse_repeats(out)
