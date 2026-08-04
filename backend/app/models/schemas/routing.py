"""Shared routing taxonomy for triage, skills, tone profiles, and feedback."""

from __future__ import annotations

from typing import Literal

RoutingCategory = Literal[
    "billing",
    "scheduling",
    "escalation",
    "vendor",
    "internal",
    "general",
]

ROUTING_CATEGORIES: tuple[RoutingCategory, ...] = (
    "billing",
    "scheduling",
    "escalation",
    "vendor",
    "internal",
    "general",
)

RejectReasonCode = Literal[
    "tone",
    "factual",
    "wrong_action",
    "incomplete",
    "policy",
    "recipients",
    "other",
]

REJECT_REASON_CODES: tuple[RejectReasonCode, ...] = (
    "tone",
    "factual",
    "wrong_action",
    "incomplete",
    "policy",
    "recipients",
    "other",
)

_ROUTING_ALIASES: dict[str, RoutingCategory] = {
    "billing": "billing",
    "invoice": "billing",
    "payment": "billing",
    "scheduling": "scheduling",
    "schedule": "scheduling",
    "escalation": "escalation",
    "escalate": "escalation",
    "vendor": "vendor",
    "internal": "internal",
    "general": "general",
    "other": "general",
}


def normalize_routing_category(raw: str | None) -> RoutingCategory:
    """Map free-text / legacy skill categories onto the closed taxonomy."""
    if raw is None or not raw.strip():
        return "general"
    key = raw.strip().lower()
    if key in _ROUTING_ALIASES:
        return _ROUTING_ALIASES[key]
    for category in ROUTING_CATEGORIES:
        if category in key or key in category:
            return category
    return "general"
