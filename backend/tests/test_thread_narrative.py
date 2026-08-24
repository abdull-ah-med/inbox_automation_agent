"""Human narrative mapper over audit payloads.

Oracles: known event_type + payload → fixed title/body strings Elise can read.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.thread_narrative import NarrativeEntry, build_activity


def test_sent_reply_resolve_has_human_title_and_urgency_note() -> None:
    entries = build_activity(
        [
            {
                "event_type": "thread.resolved.sent_reply_detected",
                "created_at": datetime(2026, 8, 21, 15, 0, tzinfo=UTC),
                "payload": {
                    "human": {
                        "title": "Resolved from sent reply",
                        "body": (
                            "Matched your Outlook send to a recent draft. "
                            "Removed from Needs Attention. Assessed urgency was HIGH; "
                            "it no longer drives priority."
                        ),
                        "actor_kind": "agent",
                    },
                    "matched_by": "time_window",
                    "urgency_assessed": "HIGH",
                },
            }
        ]
    )
    assert len(entries) == 1
    assert isinstance(entries[0], NarrativeEntry)
    assert entries[0].title == "Resolved from sent reply"
    assert "Needs Attention" in entries[0].body
    assert "HIGH" in entries[0].body
    assert entries[0].actor_kind == "agent"


def test_falls_back_when_human_payload_missing() -> None:
    entries = build_activity(
        [
            {
                "event_type": "thread.resolved.sent_reply_detected",
                "created_at": datetime(2026, 8, 21, 15, 0, tzinfo=UTC),
                "payload": {"matched_by": "approved_draft"},
            }
        ]
    )
    assert entries[0].title == "Resolved from sent reply"
    assert "approved draft" in entries[0].body.lower() or "Approved" in entries[0].body


def test_urgency_recurrence_narrative() -> None:
    entries = build_activity(
        [
            {
                "event_type": "thread.urgency.recurrence_escalated",
                "created_at": datetime(2026, 8, 21, 16, 0, tzinfo=UTC),
                "payload": {
                    "human": {
                        "title": "Urgency raised for recurring alert",
                        "body": "3rd automated alert in 48h. Urgency raised to CRITICAL.",
                        "actor_kind": "agent",
                    },
                    "floor": "CRITICAL",
                    "count_48h": 3,
                },
            }
        ]
    )
    assert entries[0].title.startswith("Urgency raised")
    assert "CRITICAL" in entries[0].body



def test_recurrence_narrative_mentions_similar_alerts() -> None:
    entries = build_activity(
        [
            {
                "event_type": "thread.urgency.recurrence_escalated",
                "created_at": datetime(2026, 8, 21, 16, 0, tzinfo=UTC),
                "payload": {
                    "human": {
                        "title": "Urgency raised for recurring alert",
                        "body": (
                            "Urgency bumped automatically: 3 similar alerts in 48h "
                            "(same sender and subject)."
                        ),
                        "actor_kind": "agent",
                    },
                    "floor": "CRITICAL",
                    "count_48h": 3,
                    "scope": "cross_thread",
                },
            }
        ]
    )
    assert entries[0].title.startswith("Urgency raised")
    assert "similar alerts" in entries[0].body


def test_recurrence_wrong_narrative() -> None:
    entries = build_activity(
        [
            {
                "event_type": "thread.urgency.recurrence_wrong",
                "created_at": datetime(2026, 8, 21, 16, 5, tzinfo=UTC),
                "payload": {
                    "human": {
                        "title": "Automatic urgency bump marked wrong",
                        "body": (
                            "Reverted this thread. This alert fingerprint will not "
                            "auto-bump again."
                        ),
                        "actor_kind": "elise",
                    },
                },
            }
        ]
    )
    assert "wrong" in entries[0].title.lower()
    assert "fingerprint" in entries[0].body.lower() or "alert" in entries[0].body.lower()
