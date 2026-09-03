"""Needs Attention queue: Elise still must act. Hand-counted fixtures.

Oracle (not derived from list_needs_attention SQL):

Mailbox sales@example.com
  T_human:              REQUIRES_HUMAN, no draft, CRITICAL, 10:00 → IN (1st)
  T_open:               DRAFTED, latest draft no feedback, HIGH, 11:00 → IN (2nd)
  T_tone:               DRAFTED, latest draft reject/tone, NORMAL, 12:00 → IN (3rd)
  T_old_wrong_new:      older wrong draft + newer unanswered draft, LOW, 13:00 → IN (4th)
  T_wrong:              DRAFTED, latest draft feedback_action=wrong → OUT
  T_approved:           DRAFTED, latest draft approved_at set → OUT
  T_resolved:           RESOLVED → OUT
  T_no_action:          NO_ACTION → OUT
  T_awaiting_client:    AWAITING_CLIENT, no feedback → OUT (not Elise's action)
Isolation
  T_other:              other@example.com DRAFTED, no feedback → not in sales queue
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.tenant_scope import TenantScope
from app.models.db.draft import Draft
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.repositories import thread_repo

pytestmark = pytest.mark.db

SALES = "sales@example.com"
OTHER = "other@example.com"

T_HUMAN = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
T_OPEN = datetime(2026, 8, 18, 11, 0, tzinfo=UTC)
T_TONE = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
T_REOPEN = datetime(2026, 8, 18, 13, 0, tzinfo=UTC)
T_OLD = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)


def _thread(
    *,
    mailbox: str,
    conversation_id: str,
    state: str,
    urgency: str | None,
    last_message_at: datetime,
) -> Thread:
    return Thread(
        id=uuid.uuid4(),
        mailbox=mailbox,
        conversation_id=conversation_id,
        subject=conversation_id,
        state=state,
        urgency=urgency,
        last_message_at=last_message_at,
    )


def _draft(
    thread: Thread,
    *,
    created_at: datetime,
    feedback_action: str | None = None,
    approved_at: datetime | None = None,
    rejected_at: datetime | None = None,
    reason: str | None = None,
) -> Draft:
    return Draft(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=str(uuid.uuid4()),
        subject=thread.subject,
        body="draft",
        recipients={},
        teaching_note="note",
        created_at=created_at,
        feedback_action=feedback_action,
        approved_at=approved_at,
        rejected_at=rejected_at,
        feedback_reason_code=reason,
    )


async def _seed(session) -> dict[str, uuid.UUID]:
    t_human = _thread(
        mailbox=SALES,
        conversation_id="t-human",
        state=ThreadStateEnum.REQUIRES_HUMAN.value,
        urgency="CRITICAL",
        last_message_at=T_HUMAN,
    )
    t_open = _thread(
        mailbox=SALES,
        conversation_id="t-open",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=T_OPEN,
    )
    t_tone = _thread(
        mailbox=SALES,
        conversation_id="t-tone",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=T_TONE,
    )
    t_reopen = _thread(
        mailbox=SALES,
        conversation_id="t-old-wrong-new",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="LOW",
        last_message_at=T_REOPEN,
    )
    t_wrong = _thread(
        mailbox=SALES,
        conversation_id="t-wrong",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=T_OPEN,
    )
    t_approved = _thread(
        mailbox=SALES,
        conversation_id="t-approved",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=T_OPEN,
    )
    t_resolved = _thread(
        mailbox=SALES,
        conversation_id="t-resolved",
        state=ThreadStateEnum.RESOLVED.value,
        urgency="HIGH",
        last_message_at=T_OPEN,
    )
    t_no_action = _thread(
        mailbox=SALES,
        conversation_id="t-no-action",
        state=ThreadStateEnum.NO_ACTION.value,
        urgency=None,
        last_message_at=T_OPEN,
    )
    t_awaiting = _thread(
        mailbox=SALES,
        conversation_id="t-awaiting-client",
        state=ThreadStateEnum.AWAITING_CLIENT.value,
        urgency="HIGH",
        last_message_at=T_OPEN,
    )
    t_other = _thread(
        mailbox=OTHER,
        conversation_id="t-other",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="CRITICAL",
        last_message_at=T_REOPEN,
    )
    session.add_all(
        [
            t_human,
            t_open,
            t_tone,
            t_reopen,
            t_wrong,
            t_approved,
            t_resolved,
            t_no_action,
            t_awaiting,
            t_other,
        ]
    )
    await session.flush()
    session.add_all(
        [
            _draft(t_open, created_at=T_OPEN),
            _draft(
                t_tone,
                created_at=T_TONE,
                feedback_action="reject",
                rejected_at=T_TONE,
                reason="tone",
            ),
            _draft(
                t_reopen,
                created_at=T_OLD,
                feedback_action="wrong",
                reason="wrong_action",
            ),
            _draft(t_reopen, created_at=T_REOPEN),
            _draft(
                t_wrong,
                created_at=T_OPEN,
                feedback_action="wrong",
                reason="wrong_action",
            ),
            _draft(t_approved, created_at=T_OPEN, approved_at=T_OPEN),
            _draft(t_other, created_at=T_REOPEN),
        ]
    )
    await session.commit()
    return {
        "human": t_human.id,
        "open": t_open.id,
        "tone": t_tone.id,
        "reopen": t_reopen.id,
        "wrong": t_wrong.id,
        "approved": t_approved.id,
        "resolved": t_resolved.id,
        "no_action": t_no_action.id,
        "awaiting": t_awaiting.id,
        "other": t_other.id,
    }


@pytest.mark.asyncio
async def test_needs_attention_sorts_by_most_recent(db_session) -> None:
    ids = await _seed(db_session)

    rows = await thread_repo.list_needs_attention(
        db_session,
        [SALES],
        limit=20,
        sort="recent",
    )
    got = [row.id for row in rows]

    assert got == [ids["reopen"], ids["tone"], ids["open"], ids["human"]]


@pytest.mark.asyncio
async def test_needs_attention_is_the_four_threads_elise_must_act_on(db_session) -> None:
    ids = await _seed(db_session)

    rows = await thread_repo.list_needs_attention(db_session, [SALES], limit=20)
    got = [row.id for row in rows]

    assert got == [ids["human"], ids["open"], ids["tone"], ids["reopen"]]


@pytest.mark.asyncio
async def test_list_by_mailbox_awaiting_action_is_needs_attention_grain(db_session) -> None:
    """Mailbox `state=AWAITING_ACTION` is the same four sales threads as Needs Attention.

    Newest-first list order (not urgency rank): reopen 13:00, tone 12:00,
    open 11:00, human 10:00. Waiting-on-client, approved, wrong-action,
    resolved, no-action, and the other mailbox are out.
    """
    ids = await _seed(db_session)

    items, _ = await thread_repo.list_by_mailbox(
        db_session, SALES, state="AWAITING_ACTION", limit=25
    )
    got = [row.id for row in items]

    assert got == [ids["reopen"], ids["tone"], ids["open"], ids["human"]]


@pytest.mark.asyncio
async def test_list_by_mailbox_stale_is_old_needs_attention_only(db_session) -> None:
    """`state=STALE` is awaiting-action threads whose last mail is older than 24h.

    Fresh open draft is out. Approved/wrong/client-waiting/other-mailbox
    stale rows are out even when last_message_at is old.
    """
    now = datetime.now(UTC)
    stale_at = now - timedelta(hours=48)
    fresh_at = now - timedelta(hours=2)

    t_stale = _thread(
        mailbox=SALES,
        conversation_id="t-stale-open",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=stale_at,
    )
    t_fresh = _thread(
        mailbox=SALES,
        conversation_id="t-fresh-open",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=fresh_at,
    )
    t_approved = _thread(
        mailbox=SALES,
        conversation_id="t-stale-approved",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=stale_at,
    )
    t_client = _thread(
        mailbox=SALES,
        conversation_id="t-stale-client",
        state=ThreadStateEnum.AWAITING_CLIENT.value,
        urgency="HIGH",
        last_message_at=stale_at,
    )
    t_other = _thread(
        mailbox=OTHER,
        conversation_id="t-stale-other",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="CRITICAL",
        last_message_at=stale_at,
    )
    db_session.add_all([t_stale, t_fresh, t_approved, t_client, t_other])
    await db_session.flush()
    db_session.add_all(
        [
            _draft(t_stale, created_at=stale_at),
            _draft(t_fresh, created_at=fresh_at),
            _draft(t_approved, created_at=stale_at, approved_at=stale_at),
            _draft(t_other, created_at=stale_at),
        ]
    )
    await db_session.commit()

    items, _ = await thread_repo.list_by_mailbox(db_session, SALES, state="STALE", limit=25)

    assert [row.id for row in items] == [t_stale.id]


@pytest.mark.asyncio
async def test_list_by_mailbox_filtered_is_spam_only(db_session) -> None:
    """`state=FILTERED` is spam only — NO_ACTION is an DraftAssistant resolve, not filtered."""
    now = datetime.now(UTC)
    t_spam = _thread(
        mailbox=SALES,
        conversation_id="t-spam",
        state=ThreadStateEnum.SPAM.value,
        urgency=None,
        last_message_at=now,
    )
    t_no_action = _thread(
        mailbox=SALES,
        conversation_id="t-filtered-no-action",
        state=ThreadStateEnum.NO_ACTION.value,
        urgency=None,
        last_message_at=now - timedelta(hours=1),
    )
    t_drafted = _thread(
        mailbox=SALES,
        conversation_id="t-still-open",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=now,
    )
    t_other = _thread(
        mailbox=OTHER,
        conversation_id="t-other-spam",
        state=ThreadStateEnum.SPAM.value,
        urgency=None,
        last_message_at=now,
    )
    db_session.add_all([t_spam, t_no_action, t_drafted, t_other])
    await db_session.commit()

    items, _ = await thread_repo.list_by_mailbox(db_session, SALES, state="FILTERED", limit=25)

    assert [row.id for row in items] == [t_spam.id]


@pytest.mark.asyncio
async def test_awaiting_action_count_matches_needs_attention_grain(db_session) -> None:
    await _seed(db_session)

    overview = await thread_repo.aggregate_overview(db_session, [SALES, OTHER])
    by_mailbox = {row["mailbox"]: row["awaiting_action_count"] for row in overview}

    assert by_mailbox[SALES] == 4
    assert by_mailbox[OTHER] == 1


@pytest.mark.asyncio
async def test_urgency_breakdown_counts_only_needs_attention_threads(db_session) -> None:
    """Dashboard bar is live queue urgency, not every stored CRITICAL/HIGH.

    Seeded Needs Attention on sales: CRITICAL, HIGH, NORMAL, LOW → 1 each.
    RESOLVED + AWAITING_CLIENT keep HIGH but are out of the bar.
    OTHER mailbox CRITICAL is isolated to that mailbox.
    """
    await _seed(db_session)

    overview = await thread_repo.aggregate_overview(db_session, [SALES, OTHER])
    by_mailbox = {row["mailbox"]: row["urgency_breakdown"] for row in overview}

    assert by_mailbox[SALES] == {
        "CRITICAL": 1,
        "HIGH": 1,
        "NORMAL": 1,
        "LOW": 1,
    }
    assert by_mailbox[OTHER] == {
        "CRITICAL": 1,
        "HIGH": 0,
        "NORMAL": 0,
        "LOW": 0,
    }


@pytest.mark.asyncio
async def test_wrong_action_sets_no_action_and_leaves_queue(db_session) -> None:
    thread = _thread(
        mailbox=SALES,
        conversation_id="t-mark-wrong",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=T_OPEN,
    )
    db_session.add(thread)
    await db_session.flush()
    draft = _draft(thread, created_at=T_OPEN)
    db_session.add(draft)
    await db_session.commit()

    from app.services import draft_feedback_service

    await draft_feedback_service.mark_wrong(
        db_session,
        draft.id,
        feedback_note="No reply needed for this drip.",
        reason_code="wrong_action",
        actor="elise@example.com",
    )
    await db_session.commit()

    updated = await thread_repo.get_by_id(db_session, thread.id, TenantScope.single(thread.mailbox))
    assert updated is not None
    assert updated.state == ThreadStateEnum.RESOLVED.value
    rows = await thread_repo.list_needs_attention(db_session, [SALES], limit=20)
    assert [row.id for row in rows] == []


@pytest.mark.asyncio
async def test_tone_reject_keeps_thread_in_queue(db_session) -> None:
    thread = _thread(
        mailbox=SALES,
        conversation_id="t-tone-reject",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=T_OPEN,
    )
    db_session.add(thread)
    await db_session.flush()
    draft = _draft(thread, created_at=T_OPEN)
    db_session.add(draft)
    await db_session.commit()

    from app.services import draft_feedback_service

    await draft_feedback_service.reject_draft(
        db_session,
        draft.id,
        feedback_note="Too casual.",
        reason_code="tone",
        actor="elise@example.com",
    )
    await db_session.commit()

    updated = await thread_repo.get_by_id(db_session, thread.id, TenantScope.single(thread.mailbox))
    assert updated is not None
    assert updated.state == ThreadStateEnum.DRAFTED.value
    rows = await thread_repo.list_needs_attention(db_session, [SALES], limit=20)
    assert [row.id for row in rows] == [thread.id]


def _message(thread: Thread, *, automated: bool, body: str) -> Message:
    return Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender="alerts@example.com" if automated else "coworker@example.com",
        body_text=body,
        received_at=thread.last_message_at or T_OPEN,
        to_recipients=[thread.mailbox],
        cc_recipients=[],
        is_automated=automated,
    )


@pytest.mark.asyncio
async def test_fyi_briefing_without_letter_is_out_of_needs_attention(db_session) -> None:
    """Briefing-only DRAFTED thread with empty body is out of Needs Attention."""
    t_fyi = _thread(
        mailbox=SALES,
        conversation_id="t-samba-fyi",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="LOW",
        last_message_at=T_OPEN,
    )
    t_letter = _thread(
        mailbox=SALES,
        conversation_id="t-letter-ready",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=T_TONE,
    )
    db_session.add_all([t_fyi, t_letter])
    await db_session.flush()
    db_session.add_all(
        [
            _message(t_fyi, automated=False, body="I now have access to Samba Safety's website."),
        ]
    )
    fyi_briefing = _draft(t_fyi, created_at=T_OPEN)
    fyi_briefing.body = ""
    db_session.add(fyi_briefing)
    letter = _draft(t_letter, created_at=T_TONE)
    letter.body = "Hi — confirming portal access is ready."
    db_session.add(letter)
    await db_session.commit()

    rows = await thread_repo.list_needs_attention(db_session, [SALES], limit=20)
    assert [row.id for row in rows] == [t_letter.id]


@pytest.mark.asyncio
async def test_automated_alert_without_letter_stays_in_needs_attention(db_session) -> None:
    """Daily Drivers: offline action, empty letter, still Elise's queue."""
    from app.models.db.audit_event import AuditEvent

    t_alert = _thread(
        mailbox=SALES,
        conversation_id="t-daily-drivers",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=T_OPEN,
    )
    t_alert.alert_fingerprint = "daily-drivers-v1"
    db_session.add(t_alert)
    await db_session.flush()
    empty = _draft(t_alert, created_at=T_OPEN)
    empty.body = ""
    db_session.add_all(
        [
            _message(t_alert, automated=True, body="Daily Drivers changes update."),
            empty,
            AuditEvent(
                event_type="triage.action_needed",
                conversation_id=t_alert.conversation_id,
                mailbox=SALES,
                payload={"has_action_items": True, "is_automated": True, "draft_needed": False},
                actor="system",
            ),
        ]
    )
    await db_session.commit()

    rows = await thread_repo.list_needs_attention(db_session, [SALES], limit=20)
    assert [row.id for row in rows] == [t_alert.id]


@pytest.mark.asyncio
async def test_informational_automated_domain_auth_is_open_fyi_not_needs_attention(
    db_session,
) -> None:
    """Domain authenticated: automated + triage no action → Open FYI, not live fire."""
    from app.models.db.audit_event import AuditEvent

    t_domain = _thread(
        mailbox=SALES,
        conversation_id="t-domain-auth",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="CRITICAL",
        last_message_at=T_OPEN,
    )
    t_domain.alert_fingerprint = "domain-auth-v1"
    db_session.add(t_domain)
    await db_session.flush()
    empty = _draft(t_domain, created_at=T_OPEN)
    empty.body = ""
    db_session.add_all(
        [
            _message(t_domain, automated=True, body="Your domain is now authenticated."),
            empty,
            AuditEvent(
                event_type="triage.no_action_discarded",
                conversation_id=t_domain.conversation_id,
                mailbox=SALES,
                payload={"has_action_items": False, "is_automated": True, "draft_needed": False},
                actor="system",
            ),
        ]
    )
    await db_session.commit()

    needs = await thread_repo.list_needs_attention(db_session, [SALES], limit=20)
    fyi = await thread_repo.list_open_fyi(db_session, [SALES], limit=20)
    assert [row.id for row in needs] == []
    assert [row.id for row in fyi] == [t_domain.id]
    assert fyi[0].urgency == "CRITICAL"
    assert fyi[0].presentation is not None
    assert fyi[0].presentation.urgency_active is False
    assert fyi[0].presentation.disposition == "fyi_briefing"

    overview = await thread_repo.aggregate_overview(db_session, [SALES])
    assert overview[0]["urgency_breakdown"]["CRITICAL"] == 0
    assert overview[0]["open_fyi_count"] == 1
    assert overview[0]["awaiting_action_count"] == 0


@pytest.mark.asyncio
async def test_open_fyi_list_is_briefing_only(db_session) -> None:
    """Open FYI list includes briefing-only drafts; letter and alert threads stay out."""
    t_fyi = _thread(
        mailbox=SALES,
        conversation_id="t-open-fyi",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="LOW",
        last_message_at=T_TONE,
    )
    t_letter = _thread(
        mailbox=SALES,
        conversation_id="t-fyi-letter",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=T_OPEN,
    )
    t_alert = _thread(
        mailbox=SALES,
        conversation_id="t-fyi-alert",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=T_HUMAN,
    )
    t_alert.alert_fingerprint = "daily-drivers-v1"
    t_other = _thread(
        mailbox=OTHER,
        conversation_id="t-other-fyi",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="LOW",
        last_message_at=T_TONE,
    )
    db_session.add_all([t_fyi, t_letter, t_alert, t_other])
    await db_session.flush()
    empty_fyi = _draft(t_fyi, created_at=T_TONE)
    empty_fyi.body = ""
    empty_other = _draft(t_other, created_at=T_TONE)
    empty_other.body = ""
    empty_alert = _draft(t_alert, created_at=T_HUMAN)
    empty_alert.body = ""
    letter = _draft(t_letter, created_at=T_OPEN)
    letter.body = "Hi — confirming portal access is ready."
    db_session.add_all(
        [
            _message(t_fyi, automated=False, body="Samba Safety website access is ready."),
            _message(t_alert, automated=True, body="Daily Drivers changes update."),
            empty_fyi,
            empty_other,
            empty_alert,
            letter,
        ]
    )
    await db_session.commit()

    rows = await thread_repo.list_open_fyi(db_session, [SALES], limit=20)
    assert [row.id for row in rows] == [t_fyi.id]

    overview = await thread_repo.aggregate_overview(db_session, [SALES, OTHER])
    by_mailbox = {row["mailbox"]: row for row in overview}
    assert by_mailbox[SALES]["open_fyi_count"] == 1
    assert by_mailbox[OTHER]["open_fyi_count"] == 1


@pytest.mark.asyncio
async def test_list_by_mailbox_reply_review_is_letter_only(db_session) -> None:
    """`state=REPLY_REVIEW` is disposition reply_review — non-empty letter, unanswered.

    Empty-body FYI and Daily Drivers action-without-letter stay out even though
    all three rows are still DRAFTED in threads.state.
    """
    from app.models.db.audit_event import AuditEvent

    t_letter = _thread(
        mailbox=SALES,
        conversation_id="t-rr-letter",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=T_TONE,
    )
    t_fyi = _thread(
        mailbox=SALES,
        conversation_id="t-rr-fyi",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="LOW",
        last_message_at=T_OPEN,
    )
    t_alert = _thread(
        mailbox=SALES,
        conversation_id="t-rr-alert",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=T_HUMAN,
    )
    t_alert.alert_fingerprint = "daily-drivers-v1"
    t_approved = _thread(
        mailbox=SALES,
        conversation_id="t-rr-approved",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="HIGH",
        last_message_at=T_REOPEN,
    )
    db_session.add_all([t_letter, t_fyi, t_alert, t_approved])
    await db_session.flush()
    empty_fyi = _draft(t_fyi, created_at=T_OPEN)
    empty_fyi.body = ""
    empty_alert = _draft(t_alert, created_at=T_HUMAN)
    empty_alert.body = ""
    letter = _draft(t_letter, created_at=T_TONE)
    letter.body = "Hi — here is the reply for review."
    approved = _draft(
        t_approved,
        created_at=T_REOPEN,
        approved_at=T_REOPEN,
    )
    approved.body = "Already approved reply body."
    db_session.add_all(
        [
            _message(t_fyi, automated=False, body="FYI portal access is ready."),
            _message(t_alert, automated=True, body="Daily Drivers changes update."),
            empty_fyi,
            empty_alert,
            letter,
            approved,
            AuditEvent(
                event_type="triage.action_needed",
                conversation_id=t_alert.conversation_id,
                mailbox=SALES,
                payload={"has_action_items": True, "is_automated": True, "draft_needed": False},
                actor="system",
            ),
        ]
    )
    await db_session.commit()

    items, _ = await thread_repo.list_by_mailbox(db_session, SALES, state="REPLY_REVIEW", limit=25)
    assert [row.id for row in items] == [t_letter.id]
    assert items[0].has_letter is True
    assert items[0].presentation is not None
    assert items[0].presentation.disposition == "reply_review"


@pytest.mark.asyncio
async def test_recently_resolved_by_draftassistant_is_48h_excluding_elise(db_session) -> None:
    """48h DraftAssistant resolves in; Elise resolve and 72h-old DraftAssistant resolve out."""
    from app.models.db.audit_event import AuditEvent

    now = datetime.now(UTC)
    t_draftassistant = _thread(
        mailbox=SALES,
        conversation_id="t-draftassistant-recent",
        state=ThreadStateEnum.NO_ACTION.value,
        urgency="LOW",
        last_message_at=now - timedelta(hours=2),
    )
    t_elise = _thread(
        mailbox=SALES,
        conversation_id="t-elise-recent",
        state=ThreadStateEnum.RESOLVED.value,
        urgency="NORMAL",
        last_message_at=now - timedelta(hours=1),
    )
    t_old = _thread(
        mailbox=SALES,
        conversation_id="t-draftassistant-old",
        state=ThreadStateEnum.RESOLVED.value,
        urgency="LOW",
        last_message_at=now - timedelta(hours=72),
    )
    db_session.add_all([t_draftassistant, t_elise, t_old])
    await db_session.flush()
    t_draftassistant.last_updated_at = now - timedelta(hours=2)
    t_elise.last_updated_at = now - timedelta(hours=1)
    t_old.last_updated_at = now - timedelta(hours=72)
    db_session.add(
        AuditEvent(
            event_type="thread.resolved.reviewer",
            conversation_id=t_elise.conversation_id,
            mailbox=SALES,
            payload={"resolved_by": "elise"},
            actor="elise@example.com",
        )
    )
    await db_session.commit()

    rows = await thread_repo.list_recently_resolved_by_draftassistant(db_session, [SALES], limit=20)
    assert [row.id for row in rows] == [t_draftassistant.id]

    overview = await thread_repo.aggregate_overview(db_session, [SALES])
    assert overview[0]["recently_resolved_draftassistant_count"] == 1
