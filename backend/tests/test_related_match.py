"""Sibling/associated matching oracles. Independent of search_threads SQL.

Worked example (Elise / SampleClient drips):
  Source:  SampleClient follow-up 8/9  from rep@sample-client.example.com  cosine 0.50
  Sibling: SampleClient follow-up 8/14 from rep@sample-client.example.com  cosine 0.50 → IN (date-stripped subject)
  Invoice: January invoice          from rep@sample-client.example.com  cosine 0.40 → OUT
Both sibling and invoice are still in Needs Attention. Cosine is below 0.78 for both,
so date-stripped subject is the only reason SampleClient is kept.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.services.related_match import (
    RelatedCandidate,
    alert_fingerprint,
    base_subject,
    deadlines_overlap,
    select_related,
    strip_calendar_dates,
)

SAMPLECLIENT_8_9 = UUID("11111111-1111-1111-1111-111111111111")
SAMPLECLIENT_8_14 = UUID("22222222-2222-2222-2222-222222222222")
INVOICE = UUID("33333333-3333-3333-3333-333333333333")
SEMANTIC = UUID("44444444-4444-4444-4444-444444444444")
DISMISSED = UUID("55555555-5555-5555-5555-555555555555")
RESOLVED_ASSOC = UUID("66666666-6666-6666-6666-666666666666")

SENDER = "rep@sample-client.example.com"
WHEN = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)


DISK_90 = UUID("77777777-7777-7777-7777-777777777777")
DISK_91 = UUID("88888888-8888-8888-8888-888888888888")
DISK_DB2 = UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
CPU_90 = UUID("cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa")
DEADLINE_REMINDER = UUID("99999999-9999-9999-9999-999999999999")
FAR_ALERT = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

ALERT_SENDER = "alerts@ops.example"
DISK_90_SUBJECT = "ALERT: Disk 90% on db-1"
DISK_91_SUBJECT = "ALERT: Disk 91% on db-1"


def _cand(
    thread_id: UUID,
    *,
    subject: str,
    cosine: float | None,
    score: float = 0.02,
    sender: str = SENDER,
    last_message_at: datetime = WHEN,
    conversation_id: str | None = None,
    trigram: float | None = None,
    deadlines: tuple[str, ...] = (),
) -> RelatedCandidate:
    return RelatedCandidate(
        thread_id=thread_id,
        mailbox="sales@example.com",
        conversation_id=conversation_id or str(thread_id),
        subject=subject,
        sender=sender,
        last_message_at=last_message_at,
        urgency="NORMAL",
        score=score,
        cosine=cosine,
        trigram=trigram,
        deadlines=deadlines,
    )


def test_strip_calendar_dates_makes_sampleclient_drips_equal() -> None:
    a = strip_calendar_dates("SampleClient follow-up 8/9")
    b = strip_calendar_dates("SampleClient follow-up 8/14")
    c = strip_calendar_dates("SampleClient follow-up August 9")
    d = strip_calendar_dates("SampleClient follow-up 2026-08-09")
    assert a == b == c == d
    assert a == "SampleClient follow-up"


def test_select_siblings_keeps_sampleclient_drip_not_invoice() -> None:
    source = _cand(SAMPLECLIENT_8_9, subject="SampleClient follow-up 8/9", cosine=0.50)
    sampleclient = _cand(SAMPLECLIENT_8_14, subject="SampleClient follow-up 8/14", cosine=0.50)
    invoice = _cand(INVOICE, subject="January invoice", cosine=0.40)

    got = select_related(
        source,
        [sampleclient, invoice],
        purpose="siblings",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids={SAMPLECLIENT_8_14, INVOICE},
    )

    assert [row.thread_id for row in got] == [SAMPLECLIENT_8_14]


def test_select_siblings_keeps_high_cosine_even_when_subject_differs() -> None:
    source = _cand(SAMPLECLIENT_8_9, subject="SampleClient follow-up 8/9", cosine=0.50)
    semantic = _cand(SEMANTIC, subject="Prior SampleClient contract Q2", cosine=0.86)

    got = select_related(
        source,
        [semantic],
        purpose="siblings",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids={SEMANTIC},
    )

    assert [row.thread_id for row in got] == [SEMANTIC]


def test_select_siblings_drops_dismissed_and_threads_not_in_queue() -> None:
    source = _cand(SAMPLECLIENT_8_9, subject="SampleClient follow-up 8/9", cosine=0.90)
    dismissed = _cand(DISMISSED, subject="SampleClient follow-up 8/1", cosine=0.90)
    resolved = _cand(RESOLVED_ASSOC, subject="SampleClient follow-up 7/1", cosine=0.90)

    got = select_related(
        source,
        [dismissed, resolved],
        purpose="siblings",
        dismissed_ids={DISMISSED},
        min_similarity=0.78,
        queue_ids=set(),
    )

    assert [row.thread_id for row in got] == []


def test_select_associated_includes_resolved_and_excludes_dismissed() -> None:
    source = _cand(SAMPLECLIENT_8_9, subject="SampleClient follow-up 8/9", cosine=0.90)
    dismissed = _cand(DISMISSED, subject="SampleClient follow-up 8/1", cosine=0.90)
    resolved = _cand(RESOLVED_ASSOC, subject="SampleClient follow-up 7/1", cosine=0.90)

    got = select_related(
        source,
        [dismissed, resolved],
        purpose="associated",
        dismissed_ids={DISMISSED},
        min_similarity=0.78,
        queue_ids=set(),
    )

    assert [row.thread_id for row in got] == [RESOLVED_ASSOC]


def test_select_related_caps_at_five_score_then_recency() -> None:
    source = _cand(SAMPLECLIENT_8_9, subject="SampleClient follow-up 8/9", cosine=0.90)
    older = datetime(2026, 8, 1, tzinfo=UTC)
    newer = datetime(2026, 8, 14, tzinfo=UTC)
    top = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
    newer_ids = [
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2"),
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3"),
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa4"),
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa5"),
    ]
    older_ids = [
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa6"),
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa7"),
    ]
    candidates = [
        _cand(
            top,
            subject="SampleClient follow-up 8/2",
            cosine=0.90,
            score=0.05,
            last_message_at=older,
        ),
        *[
            _cand(
                tid,
                subject="SampleClient follow-up 8/3",
                cosine=0.90,
                score=0.02,
                last_message_at=newer,
            )
            for tid in newer_ids
        ],
        *[
            _cand(
                tid,
                subject="SampleClient follow-up 8/4",
                cosine=0.90,
                score=0.02,
                last_message_at=older,
            )
            for tid in older_ids
        ],
    ]

    got = select_related(
        source,
        candidates,
        purpose="associated",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids=set(),
    )

    assert [row.thread_id for row in got] == [top, *newer_ids]


def test_timing_gate_drops_far_apart_low_cosine_unless_confirmed() -> None:
    """Candidates >90 days apart with weak cosine are out unless confirmed."""
    source = _cand(
        SAMPLECLIENT_8_9,
        subject="SampleClient follow-up 8/9",
        cosine=0.50,
        last_message_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
    far = _cand(
        INVOICE,
        subject="SampleClient follow-up 1/1",
        cosine=0.50,
        last_message_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    got = select_related(
        source,
        [far],
        purpose="associated",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids=set(),
        max_gap_days=90,
    )
    assert [row.thread_id for row in got] == []


def test_timing_gate_keeps_far_apart_when_confirmed() -> None:
    source = _cand(
        SAMPLECLIENT_8_9,
        subject="SampleClient follow-up 8/9",
        cosine=0.50,
        last_message_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
    far = _cand(
        RESOLVED_ASSOC,
        subject="SampleClient follow-up 1/1",
        cosine=0.50,
        last_message_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    got = select_related(
        source,
        [far],
        purpose="associated",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids=set(),
        max_gap_days=90,
        confirmed_ids={RESOLVED_ASSOC},
    )
    assert [row.thread_id for row in got] == [RESOLVED_ASSOC]


def test_dismissed_subject_template_suppresses_similar_drip() -> None:
    """After dismissing one SampleClient drip, another same-sender drip is suppressed."""
    source = _cand(SAMPLECLIENT_8_9, subject="SampleClient follow-up 8/9", cosine=0.50)
    sibling = _cand(SAMPLECLIENT_8_14, subject="SampleClient follow-up 8/14", cosine=0.50)
    got = select_related(
        source,
        [sibling],
        purpose="siblings",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids={SAMPLECLIENT_8_14},
        suppressed_drip_keys={("rep@sample-client.example.com", "SampleClient follow-up")},
    )
    assert [row.thread_id for row in got] == []



def test_base_subject_strips_alert_prefixes_and_dates() -> None:
    """RFC 5256-style prefixes plus DraftAssistant alert tokens; dates go; ticket ids stay."""
    assert base_subject("ALERT: Disk 90% on db-1") == "disk 90% on db-1"
    assert base_subject("Re: ALERT: Disk 90% on db-1") == "disk 90% on db-1"
    assert base_subject("Fwd: [monitoring] Disk 90% on db-1") == "disk 90% on db-1"
    assert base_subject("SampleClient follow-up 8/9") == "sampleclient follow-up"
    assert base_subject("SampleClient follow-up 8/14") == "sampleclient follow-up"
    assert base_subject("AW: Invoice") == "aw: invoice"
    assert base_subject("ALERT: Disk 90% on db-1 #12345") == "disk 90% on db-1 #12345"


def test_alert_fingerprint_is_mailbox_sender_base_subject_for_automated_only() -> None:
    disk = alert_fingerprint(
        mailbox="sales@example.com",
        sender=ALERT_SENDER,
        subject=DISK_90_SUBJECT,
    )
    assert disk == "sales@example.com|alerts@ops.example|disk 90% on db-1"
    human = alert_fingerprint(
        mailbox="sales@example.com",
        sender=SENDER,
        subject="SampleClient follow-up 8/9",
    )
    assert human is None


def test_deadlines_overlap_is_true_for_shared_friday_and_false_when_missing() -> None:
    assert deadlines_overlap(["Friday 8/28"], ["Friday 8/28"]) is True
    assert deadlines_overlap(
        ["complete by Friday 8/28"],
        ["Reminder: complete by Friday 8/28"],
    ) is True
    assert deadlines_overlap(["Friday 8/28"], ["Monday 8/31"]) is False
    assert deadlines_overlap([], ["Friday 8/28"]) is False
    assert deadlines_overlap(["Friday 8/28"], []) is False


def test_deadlines_overlap_false_when_same_weekday_different_dates() -> None:
    """Date-stripped remainder 'friday' must not match two different Fridays."""
    assert deadlines_overlap(["Friday 8/28"], ["Friday 9/4"]) is False
    assert deadlines_overlap(["due 8/28"], ["due 8/31"]) is False


def test_select_related_keeps_near_subject_disk_alert_not_invoice() -> None:
    source = _cand(
        DISK_90,
        subject=DISK_90_SUBJECT,
        cosine=0.50,
        sender=ALERT_SENDER,
        trigram=1.0,
        deadlines=(),
    )
    near = _cand(
        DISK_91,
        subject=DISK_91_SUBJECT,
        cosine=0.50,
        sender=ALERT_SENDER,
        trigram=0.72,
    )
    invoice = _cand(
        INVOICE,
        subject="January invoice",
        cosine=0.40,
        sender=ALERT_SENDER,
        trigram=0.12,
    )
    got = select_related(
        source,
        [near, invoice],
        purpose="associated",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids=set(),
    )
    assert [row.thread_id for row in got] == [DISK_91]
    assert "same_sender" in got[0].match_reasons
    assert "near_subject" in got[0].match_reasons


def test_select_related_keeps_metric_delta_without_trigram() -> None:
    """90% vs 91% on the same host is the same alert even when search omits trigram."""
    source = _cand(
        DISK_90,
        subject=DISK_90_SUBJECT,
        cosine=0.50,
        sender=ALERT_SENDER,
    )
    near = _cand(
        DISK_91,
        subject=DISK_91_SUBJECT,
        cosine=0.50,
        sender=ALERT_SENDER,
    )
    got = select_related(
        source,
        [near],
        purpose="associated",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids=set(),
    )
    assert [row.thread_id for row in got] == [DISK_91]
    assert "near_subject" in got[0].match_reasons


def test_select_related_drops_different_host_and_metric_even_with_high_trigram() -> None:
    """db-1 vs db-2 and disk vs cpu are different incidents (PagerDuty: host must match)."""
    source = _cand(
        DISK_90,
        subject=DISK_90_SUBJECT,
        cosine=0.50,
        sender=ALERT_SENDER,
        trigram=0.88,
    )
    other_host = _cand(
        DISK_DB2,
        subject="ALERT: Disk 90% on db-2",
        cosine=0.50,
        sender=ALERT_SENDER,
        trigram=0.88,
    )
    other_metric = _cand(
        CPU_90,
        subject="ALERT: CPU 90% on db-1",
        cosine=0.50,
        sender=ALERT_SENDER,
        trigram=0.67,
    )
    got = select_related(
        source,
        [other_host, other_metric],
        purpose="associated",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids=set(),
    )
    assert [row.thread_id for row in got] == []


def test_select_related_drops_identical_subject_from_different_sender() -> None:
    source = _cand(
        DISK_90,
        subject=DISK_90_SUBJECT,
        cosine=0.50,
        sender=ALERT_SENDER,
        trigram=1.0,
    )
    other = _cand(
        DISK_91,
        subject=DISK_90_SUBJECT,
        cosine=0.50,
        sender="pager@other.example",
        trigram=1.0,
    )
    got = select_related(
        source,
        [other],
        purpose="associated",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids=set(),
    )
    assert [row.thread_id for row in got] == []


def test_select_related_keeps_shared_deadline_despite_different_wording() -> None:
    source = _cand(
        DISK_90,
        subject="Please complete by Fri 8/28",
        cosine=0.60,
        sender=ALERT_SENDER,
        trigram=0.20,
        deadlines=("Friday 8/28",),
    )
    reminder = _cand(
        DEADLINE_REMINDER,
        subject="Reminder: complete by Friday 8/28",
        cosine=0.60,
        sender=ALERT_SENDER,
        trigram=0.20,
        deadlines=("Friday 8/28",),
    )
    got = select_related(
        source,
        [reminder],
        purpose="associated",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids=set(),
    )
    assert [row.thread_id for row in got] == [DEADLINE_REMINDER]
    assert "same_sender" in got[0].match_reasons
    assert "shared_deadline" in got[0].match_reasons


def test_select_related_drops_same_fingerprint_100_days_apart_unless_confirmed() -> None:
    source = _cand(
        DISK_90,
        subject=DISK_90_SUBJECT,
        cosine=0.50,
        sender=ALERT_SENDER,
        last_message_at=datetime(2026, 8, 14, tzinfo=UTC),
        trigram=1.0,
    )
    far = _cand(
        FAR_ALERT,
        subject=DISK_90_SUBJECT,
        cosine=0.50,
        sender=ALERT_SENDER,
        last_message_at=datetime(2026, 5, 6, tzinfo=UTC),
        trigram=1.0,
    )
    dropped = select_related(
        source,
        [far],
        purpose="associated",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids=set(),
        max_gap_days=90,
    )
    assert [row.thread_id for row in dropped] == []
    kept = select_related(
        source,
        [far],
        purpose="associated",
        dismissed_ids=set(),
        min_similarity=0.78,
        queue_ids=set(),
        max_gap_days=90,
        confirmed_ids={FAR_ALERT},
    )
    assert [row.thread_id for row in kept] == [FAR_ALERT]
