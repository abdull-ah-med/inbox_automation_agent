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
from datetime import UTC, datetime

import pytest

from app.core.tenant_scope import TenantScope
from app.models.db.draft import Draft
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
async def test_awaiting_action_count_matches_needs_attention_grain(db_session) -> None:
    await _seed(db_session)

    overview = await thread_repo.aggregate_overview(db_session, [SALES, OTHER])
    by_mailbox = {row["mailbox"]: row["awaiting_action_count"] for row in overview}

    assert by_mailbox[SALES] == 4
    assert by_mailbox[OTHER] == 1


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
    assert updated.state == ThreadStateEnum.NO_ACTION.value
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
