"""Resolution banner provenance from audit events (manual vs auto)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.thread_narrative import resolution_mode_from_events

T1 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
T3 = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)


def _event(event_type: str, when: datetime) -> dict[str, object]:
    return {"event_type": event_type, "created_at": when, "payload": {}}


def test_resolution_mode_manual_after_reopen_clears_then_reapplies() -> None:
    events = [
        _event("thread.resolved.sent_reply_detected", T1),
        _event("thread.reopened.inbound_followup", T2),
        _event("thread.resolved.reviewer", T3),
    ]
    assert resolution_mode_from_events(events) == "manual"


def test_resolution_mode_auto_when_latest_resolve_is_sent_reply() -> None:
    events = [_event("thread.resolved.sent_reply_detected", T1)]
    assert resolution_mode_from_events(events) == "auto"
