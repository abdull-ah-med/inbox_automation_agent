"""Triage audit event names shared by pipeline, audit, and ops metrics."""

from __future__ import annotations

from enum import StrEnum


class TriageAuditEvent(StrEnum):
    ACTION_NEEDED = "triage.action_needed"
    NO_ACTION_DISCARDED = "triage.no_action_discarded"
    SPAM_DISCARDED = "triage.spam_discarded"
    FAILED = "triage.failed"
