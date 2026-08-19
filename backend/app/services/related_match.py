"""Deterministic sibling/associated filters on top of hybrid search hits.

Cosine floor is ``embedding_min_similarity`` (0.78). Date-stripped subject
equality catches recurring drips that sit below that floor. Search ranking
score is used only for ordering, never as cosine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

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


def strip_calendar_dates(subject: str) -> str:
    """Remove numeric and named calendar dates so drip subjects can match."""
    stripped = _DATE_RE.sub(" ", subject or "")
    return _WS_RE.sub(" ", stripped).strip()


def normalize_sender(sender: str) -> str:
    return sender.strip().lower()


def _same_drip(source: RelatedCandidate, candidate: RelatedCandidate) -> bool:
    if normalize_sender(source.sender) != normalize_sender(candidate.sender):
        return False
    left = strip_calendar_dates(source.subject)
    right = strip_calendar_dates(candidate.subject)
    return bool(left) and left == right


def _high_cosine(candidate: RelatedCandidate, min_similarity: float) -> bool:
    return candidate.cosine is not None and candidate.cosine >= min_similarity


def select_related(
    source: RelatedCandidate,
    candidates: list[RelatedCandidate],
    *,
    purpose: RelatedPurpose,
    dismissed_ids: set[UUID],
    min_similarity: float,
    queue_ids: set[UUID],
    limit: int = 5,
) -> list[RelatedCandidate]:
    """Keep candidates that are still proposed and match cosine or drip subject."""
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
        if not (_high_cosine(candidate, min_similarity) or _same_drip(source, candidate)):
            continue
        seen.add(candidate.thread_id)
        kept.append(candidate)

    kept.sort(
        key=lambda row: (
            -row.score,
            -(row.last_message_at.timestamp() if row.last_message_at else 0.0),
        )
    )
    return kept[:limit]
