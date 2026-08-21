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

from app.services.related_match import RelatedCandidate, select_related, strip_calendar_dates

SAMPLECLIENT_8_9 = UUID("11111111-1111-1111-1111-111111111111")
SAMPLECLIENT_8_14 = UUID("22222222-2222-2222-2222-222222222222")
INVOICE = UUID("33333333-3333-3333-3333-333333333333")
SEMANTIC = UUID("44444444-4444-4444-4444-444444444444")
DISMISSED = UUID("55555555-5555-5555-5555-555555555555")
RESOLVED_ASSOC = UUID("66666666-6666-6666-6666-666666666666")

SENDER = "rep@sample-client.example.com"
WHEN = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)


def _cand(
    thread_id: UUID,
    *,
    subject: str,
    cosine: float | None,
    score: float = 0.02,
    sender: str = SENDER,
    last_message_at: datetime = WHEN,
    conversation_id: str | None = None,
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
