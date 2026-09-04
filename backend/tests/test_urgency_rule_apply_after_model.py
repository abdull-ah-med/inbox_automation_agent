"""DB tests for urgency_rule_service.apply_after_model.

Worked example — Snorkel-style LF evaluation:

Rule: sender_domain=statuspage.io → set_urgency_floor=LOW (canary status)
  - Email from statuspage.io with model HIGH → final LOW (rule fires)
  - Email from other.com with model HIGH → final HIGH (rule does not fire)

Oracle: hand-derived from the rule condition + action DSL — not derived from
the apply_after_model implementation itself.
"""

from __future__ import annotations

import pytest

from app.repositories import urgency_rule_repo
from app.services.urgency_rule_service import apply_after_model

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
STATUSPAGE_DOMAIN = "statuspage.io"
OTHER_DOMAIN = "other.com"

STATUSPAGE_CONDITION = {
    "sender_domain": "statuspage.io",
    "body_contains_any": ["resolved", "incident", "degraded"],
}
OVERRIDE_ACTION = {"set_urgency": "LOW"}
FLOOR_ACTION = {"set_urgency_floor": "LOW"}


async def test_statuspage_high_becomes_low(db_session) -> None:
    """Rule fires for statuspage.io: HIGH urgency → LOW (set_urgency=LOW)."""
    await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition=STATUSPAGE_CONDITION,
        action=OVERRIDE_ACTION,
        status="canary",
    )

    decision = await apply_after_model(
        db_session,
        mailbox=MAILBOX,
        predicted_urgency="HIGH",
        sender_domain=STATUSPAGE_DOMAIN,
        alert_fingerprint=None,
        body_text="All systems resolved. The incident is closed.",
    )

    # Oracle: the rule fires → final is LOW (not HIGH)
    assert decision.final_urgency == "LOW"
    assert len(decision.applied_rule_ids) == 1  # exactly one rule fired


async def test_urgency_floor_does_not_lower_high(db_session) -> None:
    """set_urgency_floor=LOW leaves HIGH unchanged (true floor, not override)."""
    await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition=STATUSPAGE_CONDITION,
        action=FLOOR_ACTION,
        status="canary",
    )

    decision = await apply_after_model(
        db_session,
        mailbox=MAILBOX,
        predicted_urgency="HIGH",
        sender_domain=STATUSPAGE_DOMAIN,
        alert_fingerprint=None,
        body_text="All systems resolved. The incident is closed.",
    )

    assert decision.final_urgency == "HIGH"
    assert len(decision.applied_rule_ids) == 1


async def test_other_domain_no_rule_fires(db_session) -> None:
    """Rule for statuspage.io does NOT fire for other.com — urgency unchanged."""
    await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition=STATUSPAGE_CONDITION,
        action=FLOOR_ACTION,
        status="canary",
    )

    decision = await apply_after_model(
        db_session,
        mailbox=MAILBOX,
        predicted_urgency="HIGH",
        sender_domain=OTHER_DOMAIN,
        alert_fingerprint=None,
        body_text="Please review the attached invoice.",
    )

    # Oracle: no matching rule → final equals predicted HIGH
    assert decision.final_urgency == "HIGH"
    assert len(decision.applied_rule_ids) == 0  # no rules fired


async def test_no_rules_returns_predicted_urgency(db_session) -> None:
    """With no rules in DB, apply_after_model returns predicted_urgency unchanged."""
    decision = await apply_after_model(
        db_session,
        mailbox=MAILBOX,
        predicted_urgency="CRITICAL",
        sender_domain=STATUSPAGE_DOMAIN,
        alert_fingerprint=None,
        body_text="System degraded.",
    )

    # Oracle: zero rules → passthrough
    assert decision.final_urgency == "CRITICAL"
    assert decision.applied_rule_ids == []


async def test_older_live_rule_survives_hundred_newer_archived(db_session) -> None:
    """1 older active + 100 newer archived still fires the live floor."""
    from datetime import UTC, datetime, timedelta

    from app.models.db.urgency_rule import UrgencyRule

    older = datetime.now(UTC) - timedelta(days=2)
    live = UrgencyRule(
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition={"sender_domain": STATUSPAGE_DOMAIN},
        action=OVERRIDE_ACTION,
        status="active",
        created_at=older,
    )
    db_session.add(live)
    await db_session.flush()
    for i in range(100):
        db_session.add(
            UrgencyRule(
                mailbox=MAILBOX,
                scope="sender_domain",
                scope_key=f"domain:archived-{i}.example",
                condition={"sender_domain": f"archived-{i}.example"},
                action=OVERRIDE_ACTION,
                status="archived",
                created_at=datetime.now(UTC) - timedelta(minutes=i),
            )
        )
    await db_session.flush()

    decision = await apply_after_model(
        db_session,
        mailbox=MAILBOX,
        predicted_urgency="HIGH",
        sender_domain=STATUSPAGE_DOMAIN,
        alert_fingerprint=None,
        body_text="All systems resolved.",
    )

    assert decision.final_urgency == "LOW"
    assert decision.applied_rule_ids == [live.id]


async def test_poisoned_body_contains_any_does_not_raise(db_session) -> None:
    await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition={"sender_domain": STATUSPAGE_DOMAIN, "body_contains_any": [None, 1]},
        action=OVERRIDE_ACTION,
        status="active",
    )

    decision = await apply_after_model(
        db_session,
        mailbox=MAILBOX,
        predicted_urgency="HIGH",
        sender_domain=STATUSPAGE_DOMAIN,
        alert_fingerprint=None,
        body_text="All systems resolved.",
    )

    assert decision.final_urgency == "HIGH"
    assert decision.applied_rule_ids == []


async def test_active_rule_fires(db_session) -> None:
    """A rule in 'active' status (not just canary) also fires."""
    await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition={"sender_domain": STATUSPAGE_DOMAIN},
        action=OVERRIDE_ACTION,
        status="active",
    )

    decision = await apply_after_model(
        db_session,
        mailbox=MAILBOX,
        predicted_urgency="NORMAL",
        sender_domain=STATUSPAGE_DOMAIN,
        alert_fingerprint=None,
        body_text="Maintenance complete.",
    )

    # Oracle: active rule fires → LOW
    assert decision.final_urgency == "LOW"
    assert len(decision.applied_rule_ids) == 1


async def test_flag_off_leaves_predicted_urgency_unchanged(db_session) -> None:
    """urgency_rules_enabled=False → HIGH stays HIGH even with a matching LOW rule."""
    from app.core.config import Settings

    await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition=STATUSPAGE_CONDITION,
        action=FLOOR_ACTION,
        status="canary",
    )
    settings = Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        urgency_rules_enabled=False,
    )
    decision = await apply_after_model(
        db_session,
        mailbox=MAILBOX,
        predicted_urgency="HIGH",
        sender_domain=STATUSPAGE_DOMAIN,
        alert_fingerprint=None,
        body_text="All systems resolved. The incident is closed.",
        settings=settings,
    )
    assert decision.final_urgency == "HIGH"
    assert decision.applied_rule_ids == []


async def test_set_urgency_overrides_high_to_low(db_session) -> None:
    """Payloads use action.set_urgency as a hard override."""
    from app.core.config import Settings

    await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition={"sender_domain": STATUSPAGE_DOMAIN},
        action={"set_urgency": "LOW"},
        status="active",
    )
    settings = Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        urgency_rules_enabled=True,
    )
    decision = await apply_after_model(
        db_session,
        mailbox=MAILBOX,
        predicted_urgency="HIGH",
        sender_domain=STATUSPAGE_DOMAIN,
        alert_fingerprint=None,
        body_text="Maintenance complete.",
        settings=settings,
    )
    assert decision.final_urgency == "LOW"


async def test_firing_rule_increments_hit_count(db_session) -> None:
    """A matching rule increments hit_count by 1."""
    from app.core.config import Settings

    rule = await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition={"sender_domain": STATUSPAGE_DOMAIN},
        action=FLOOR_ACTION,
        status="active",
    )
    settings = Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        urgency_rules_enabled=True,
    )
    await apply_after_model(
        db_session,
        mailbox=MAILBOX,
        predicted_urgency="HIGH",
        sender_domain=STATUSPAGE_DOMAIN,
        alert_fingerprint=None,
        body_text="ok",
        settings=settings,
    )
    fetched = await urgency_rule_repo.get_urgency_rule_by_id(db_session, rule.id)
    assert fetched is not None
    assert fetched.hit_count == 1


async def test_paused_rule_does_not_fire(db_session) -> None:
    """A rule in 'paused' status is excluded from evaluation."""
    await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition={"sender_domain": STATUSPAGE_DOMAIN},
        action=FLOOR_ACTION,
        status="paused",
    )

    decision = await apply_after_model(
        db_session,
        mailbox=MAILBOX,
        predicted_urgency="HIGH",
        sender_domain=STATUSPAGE_DOMAIN,
        alert_fingerprint=None,
        body_text="Incident resolved.",
    )

    # Oracle: paused rule is not in active_statuses → no change
    assert decision.final_urgency == "HIGH"
    assert decision.applied_rule_ids == []


async def test_resume_restores_canary_while_window_open(db_session) -> None:
    """Paused canary with canary_until in the future resumes as canary, not active."""
    from datetime import UTC, datetime, timedelta

    from app.services.urgency_rule_service import pause_rule, resume_rule

    until = datetime.now(UTC) + timedelta(days=7)
    rule = await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition={"sender_domain": STATUSPAGE_DOMAIN},
        action=FLOOR_ACTION,
        status="canary",
        canary_until=until,
    )
    paused = await pause_rule(db_session, rule.id)
    assert paused is not None
    assert paused.status == "paused"
    resumed = await resume_rule(db_session, rule.id)
    assert resumed is not None
    assert resumed.status == "canary"


async def test_resume_expired_canary_becomes_active(db_session) -> None:
    """Paused canary whose window has passed resumes as active."""
    from datetime import UTC, datetime, timedelta

    from app.services.urgency_rule_service import pause_rule, resume_rule

    until = datetime.now(UTC) - timedelta(hours=1)
    rule = await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition={"sender_domain": STATUSPAGE_DOMAIN},
        action=FLOOR_ACTION,
        status="canary",
        canary_until=until,
    )
    await pause_rule(db_session, rule.id)
    resumed = await resume_rule(db_session, rule.id)
    assert resumed is not None
    assert resumed.status == "active"


async def test_resume_expired_canary_high_override_archives(db_session) -> None:
    """Expired canary with override rate > 0.25 and n≥4 archives instead of activating."""
    from datetime import UTC, datetime, timedelta

    from app.models.db.urgency_rule import UrgencyRule
    from app.services.urgency_rule_service import pause_rule, resume_rule

    until = datetime.now(UTC) - timedelta(hours=1)
    rule = await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition={"sender_domain": STATUSPAGE_DOMAIN},
        action=FLOOR_ACTION,
        status="canary",
        canary_until=until,
    )
    row = await db_session.get(UrgencyRule, rule.id)
    assert row is not None
    row.hit_count = 8
    row.override_count = 4
    await db_session.flush()
    await pause_rule(db_session, rule.id)
    resumed = await resume_rule(db_session, rule.id)
    assert resumed is not None
    assert resumed.status == "archived"
    """An active rule paused while canary_until is still in the future resumes as active."""
    from datetime import UTC, datetime, timedelta

    from app.services.urgency_rule_service import pause_rule, resume_rule

    until = datetime.now(UTC) + timedelta(days=7)
    rule = await urgency_rule_repo.create_urgency_rule(
        db_session,
        mailbox=MAILBOX,
        scope="sender_domain",
        scope_key=f"domain:{STATUSPAGE_DOMAIN}",
        condition={"sender_domain": STATUSPAGE_DOMAIN},
        action=FLOOR_ACTION,
        status="active",
        canary_until=until,
    )
    paused = await pause_rule(db_session, rule.id)
    assert paused is not None
    assert paused.previous_status == "active"
    resumed = await resume_rule(db_session, rule.id)
    assert resumed is not None
    assert resumed.status == "active"
