"""Deterministic sibling/associated filters on top of hybrid search hits.

Cosine floor is ``embedding_min_similarity`` (0.78). Date-stripped subject
equality catches recurring drips that sit below that floor. Near-subject
uses numeric-slack signatures (PagerDuty-style: host tokens stay, metric
numbers may differ) because pg_trgm cannot separate 90% vs 91% from db-1
vs db-2. Overlapping Haiku deadlines require a shared calendar date.
Search ranking score is used only for ordering, never as cosine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.core.automated_mail import is_automated_mail

_MONTH = (
    r"January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_DATE_RE = re.compile(
    rf"""
    (?:
        \b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b
        | \b\d{{4}}-\d{{2}}-\d{{2}}\b
        | \b(?:{_MONTH})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s*\d{{4}})?\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_WS_RE = re.compile(r"\s+")
_PREFIX_RE = re.compile(
    r"^(?:re|fwd|fw|auto|alert)\s*:\s*|^\[\s*(?:alert|monitoring)\s*\]\s*",
    re.IGNORECASE,
)

_METRIC_NUM_RE = re.compile(r"^\d+(?:[.,]\d+)?%?$")
NEAR_SUBJECT_TRGM = 0.55  # pg_trgm candidate check only; not a keep/drop gate
RelatedPurpose = str  # "siblings" | "associated"


@dataclass(frozen=True, slots=True)
class RelatedCandidate:
    thread_id: UUID
    mailbox: str
    conversation_id: str
    subject: str
    sender: str
    last_message_at: datetime | None
    urgency: str | None
    score: float
    cosine: float | None
    trigram: float | None = None
    deadlines: tuple[str, ...] = ()
    match_reasons: tuple[str, ...] = ()


def strip_calendar_dates(subject: str) -> str:
    """Remove numeric and named calendar dates so drip subjects can match."""
    stripped = _DATE_RE.sub(" ", subject or "")
    return _WS_RE.sub(" ", stripped).strip()


def normalize_sender(sender: str) -> str:
    return sender.strip().lower()


def base_subject(subject: str) -> str:
    """RFC 5256-style base subject plus DraftAssistant alert prefixes; dates stripped."""
    text = subject or ""
    while True:
        nxt = _PREFIX_RE.sub("", text, count=1)
        if nxt == text:
            break
        text = nxt
    text = strip_calendar_dates(text)
    return _WS_RE.sub(" ", text).casefold().strip()


def alert_fingerprint(*, mailbox: str, sender: str, subject: str) -> str | None:
    """Stable automated-alert key: mailbox | sender | base subject."""
    keys = alert_cluster_keys(mailbox=mailbox, sender=sender, subject=subject)
    if keys is None:
        return None
    return keys[0]


def numeric_slack_subject(subject: str) -> str:
    """Replace purely numeric metric tokens with #; keep host tokens like db-1."""
    parts: list[str] = []
    for tok in base_subject(subject).split():
        parts.append("#" if _METRIC_NUM_RE.match(tok) else tok)
    return " ".join(parts)


def alert_cluster_keys(*, mailbox: str, sender: str, subject: str) -> tuple[str, str, str] | None:
    """Exact fingerprint, numeric-slack signature, and normalized sender."""
    if not is_automated_mail(sender=sender, subject=subject):
        return None
    base = base_subject(subject)
    if not base:
        return None
    sender_norm = normalize_sender(sender)
    fingerprint = f"{mailbox.strip().lower()}|{sender_norm}|{base}"
    return fingerprint, numeric_slack_subject(subject), sender_norm


def subjects_near(left: str, right: str) -> bool:
    """True when non-numeric tokens match and the metric/number tokens differ."""
    left_base = base_subject(left)
    right_base = base_subject(right)
    if not left_base or not right_base or left_base == right_base:
        return False
    return numeric_slack_subject(left) == numeric_slack_subject(right)


def _deadline_parts(raw: str) -> tuple[str, set[str]]:
    folded = _WS_RE.sub(" ", (raw or "").casefold().strip())
    dates = {match.group(0).casefold() for match in _DATE_RE.finditer(folded)}
    return strip_calendar_dates(folded), dates


def deadlines_overlap(
    left: list[str] | tuple[str, ...],
    right: list[str] | tuple[str, ...],
) -> bool:
    if not left or not right:
        return False
    left_parts = [_deadline_parts(str(item)) for item in left if str(item).strip()]
    right_parts = [_deadline_parts(str(item)) for item in right if str(item).strip()]
    if not left_parts or not right_parts:
        return False
    for left_remainder, left_dates in left_parts:
        for right_remainder, right_dates in right_parts:
            if left_dates and right_dates and not (left_dates & right_dates):
                continue
            if (
                left_remainder
                and right_remainder
                and (
                    left_remainder == right_remainder
                    or left_remainder in right_remainder
                    or right_remainder in left_remainder
                )
            ):
                return True
            if (left_dates & right_dates) and (not left_remainder or not right_remainder):
                return True
    return False


def _same_drip(source: RelatedCandidate, candidate: RelatedCandidate) -> bool:
    if normalize_sender(source.sender) != normalize_sender(candidate.sender):
        return False
    left = strip_calendar_dates(source.subject)
    right = strip_calendar_dates(candidate.subject)
    return bool(left) and left == right


def _high_cosine(candidate: RelatedCandidate, min_similarity: float) -> bool:
    return candidate.cosine is not None and candidate.cosine >= min_similarity


def _near_subject(source: RelatedCandidate, candidate: RelatedCandidate) -> bool:
    return subjects_near(source.subject, candidate.subject)


def match_reasons_for(
    source: RelatedCandidate,
    candidate: RelatedCandidate,
    *,
    cosine_kept: bool = False,
) -> tuple[str, ...]:
    reasons: list[str] = []
    same_sender = normalize_sender(source.sender) == normalize_sender(candidate.sender)
    if same_sender:
        reasons.append("same_sender")
    src_base = base_subject(source.subject)
    cand_base = base_subject(candidate.subject)
    if src_base and src_base == cand_base:
        reasons.append("same_subject")
    elif same_sender and _near_subject(source, candidate):
        reasons.append("near_subject")
    if same_sender and deadlines_overlap(source.deadlines, candidate.deadlines):
        reasons.append("shared_deadline")
    if cosine_kept:
        reasons.append("cosine")
    return tuple(reasons)


def drip_key(sender: str, subject: str) -> tuple[str, str]:
    return (normalize_sender(sender), strip_calendar_dates(subject))


def _gap_days(left: datetime | None, right: datetime | None) -> float | None:
    if left is None or right is None:
        return None
    return abs((left - right).total_seconds()) / 86_400.0


def select_related(
    source: RelatedCandidate,
    candidates: list[RelatedCandidate],
    *,
    purpose: RelatedPurpose,
    dismissed_ids: set[UUID],
    min_similarity: float,
    queue_ids: set[UUID],
    limit: int = 5,
    max_gap_days: float | None = 90.0,
    confirmed_ids: set[UUID] | None = None,
    suppressed_drip_keys: set[tuple[str, str]] | None = None,
) -> list[RelatedCandidate]:
    """Keep candidates that are still proposed and match cosine, drip, or alert signals."""
    confirmed = confirmed_ids or set()
    suppressed = suppressed_drip_keys or set()
    kept: list[RelatedCandidate] = []
    seen: set[UUID] = set()
    for candidate in candidates:
        if candidate.thread_id == source.thread_id:
            continue
        if candidate.conversation_id == source.conversation_id:
            continue
        if candidate.thread_id in seen:
            continue
        if candidate.thread_id in dismissed_ids:
            continue
        if purpose == "siblings" and candidate.thread_id not in queue_ids:
            continue
        high = _high_cosine(candidate, min_similarity)
        drip = _same_drip(source, candidate)
        same_sender = normalize_sender(source.sender) == normalize_sender(candidate.sender)
        src_base = base_subject(source.subject)
        cand_base = base_subject(candidate.subject)
        same_base = same_sender and bool(src_base) and src_base == cand_base
        near = same_sender and _near_subject(source, candidate)
        deadline = same_sender and deadlines_overlap(source.deadlines, candidate.deadlines)
        if not (high or drip or same_base or near or deadline):
            continue
        key = drip_key(candidate.sender, candidate.subject)
        if drip and key in suppressed and candidate.thread_id not in confirmed:
            continue
        if max_gap_days is not None and candidate.thread_id not in confirmed and not high:
            gap = _gap_days(source.last_message_at, candidate.last_message_at)
            if gap is not None and gap > max_gap_days:
                continue
        seen.add(candidate.thread_id)
        kept.append(
            replace(
                candidate,
                match_reasons=match_reasons_for(source, candidate, cosine_kept=high),
            )
        )

    kept.sort(
        key=lambda row: (
            -row.score,
            -(row.last_message_at.timestamp() if row.last_message_at else 0.0),
        )
    )
    return kept[:limit]
