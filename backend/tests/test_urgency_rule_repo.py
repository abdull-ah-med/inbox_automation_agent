"""DB tests for urgency_rule_repo.

Worked example — canary rule:
  - Create a canary rule for "sales@example.com" with sender_domain condition
  - list_by_mailbox_status(status="canary") returns exactly 1 row
  - list_by_mailbox_status(status="active") returns 0 rows
  - Rule fields (condition, action, scope_key) match the literal inputs
"""

from __future__ import annotations

import pytest

from app.repositories import urgency_rule_repo

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"

# Hand-crafted condition/action matching PagerDuty labelling-function pattern
CONDITION = {"sender_domain": "statuspage.io", "body_contains_any": ["resolved", "closed"]}
ACTION = {"set_urgency_floor": "LOW"}


async def test_create_canary_rule(db_session) -> None:
    """Canary rule is stored with correct condition, action, and default status."""
    rule = await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key="domain:statuspage.io",
        condition=CONDITION,
        action=ACTION,
        status="canary",
        impact_num=42,
        impact_den=42,
    )

    assert rule.status == "canary"
    assert rule.mailbox == MAILBOX
    assert rule.scope_key == "domain:statuspage.io"
    assert rule.hit_count == 0
    assert rule.override_count == 0
    # Literal oracle: exact condition dict
    assert rule.condition == {
        "sender_domain": "statuspage.io",
        "body_contains_any": ["resolved", "closed"],
    }
    assert rule.action == {"set_urgency_floor": "LOW"}
    assert rule.impact_num == 42
    assert rule.impact_den == 42


async def test_list_by_mailbox_status_canary(db_session) -> None:
    """list returns 1 canary rule; 0 active rules for same mailbox."""
    await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key="domain:statuspage.io",
        condition=CONDITION,
        action=ACTION,
        status="canary",
    )

    canary = await urgency_rule_repo.list_urgency_rules_by_mailbox_status(
        db_session, mailbox=MAILBOX, status="canary"
    )
    active = await urgency_rule_repo.list_urgency_rules_by_mailbox_status(
        db_session, mailbox=MAILBOX, status="active"
    )

    assert len(canary) == 1  # one canary rule inserted
    assert len(active) == 0  # no active rules


async def test_list_all_rules_no_status_filter(db_session) -> None:
    """list without status filter returns rules of any status."""
    await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX}",
        condition={"always": True},
        action={"set_urgency_floor": "NORMAL"},
        status="canary",
    )
    await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="mailbox",
        scope_key=f"mailbox:{MAILBOX}",
        condition={"always": False},
        action={"set_urgency_floor": "HIGH"},
        status="active",
    )

    all_rules = await urgency_rule_repo.list_urgency_rules_by_mailbox_status(
        db_session, mailbox=MAILBOX
    )
    assert len(all_rules) == 2  # 1 canary + 1 active = 2
