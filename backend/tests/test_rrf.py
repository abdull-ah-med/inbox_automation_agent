"""Unit tests for RRF fusion and conversation aggregation."""

from __future__ import annotations

import uuid

from app.services.rrf import (
    MessageHit,
    aggregate_conversation_scores,
    best_hit_for_conversation,
    rrf_fuse,
    select_related_conversations,
)


def test_rrf_fuse_sums_legs() -> None:
    a = uuid.uuid4()
    b = uuid.uuid4()
    vector = [(a, "conv-a", None), (b, "conv-b", None)]
    fts = [(b, "conv-b", None)]
    hits = rrf_fuse(vector, fts, k=60)
    by_id = {h.embedding_id: h for h in hits}
    # b appears in both legs → higher score than a
    assert by_id[b].rrf_score > by_id[a].rrf_score


def test_aggregate_worked_example_corroboration_beats_single() -> None:
    """Fix 6 worked example: three corroborating hits beat one stronger hit."""
    # Conv A: one hit at vector rank 2 → 1/(60+2)
    # Conv B: three hits at ranks 4,6,9 → max 1/(60+4) * (1+0.15*2)
    id_a = uuid.uuid4()
    id_b1 = uuid.uuid4()
    id_b2 = uuid.uuid4()
    id_b3 = uuid.uuid4()
    hits = [
        MessageHit(id_a, "conv-a", None, 1.0 / (60 + 2)),
        MessageHit(id_b1, "conv-b", None, 1.0 / (60 + 4)),
        MessageHit(id_b2, "conv-b", None, 1.0 / (60 + 6)),
        MessageHit(id_b3, "conv-b", None, 1.0 / (60 + 9)),
    ]
    scores = aggregate_conversation_scores(hits, bonus=0.15, max_bonus_hits=3)
    assert scores["conv-b"] > scores["conv-a"]
    assert abs(scores["conv-a"] - 0.016129) < 1e-5
    assert abs(scores["conv-b"] - 0.0203125) < 1e-5


def test_select_related_conversations_hard_cap_one() -> None:
    ranked = [("c1", 0.02), ("c2", 0.019)]
    assert select_related_conversations(
        ranked, max_conversations=1, margin=0.85
    ) == ["c1"]


def test_select_related_conversations_soft_margin() -> None:
    ranked = [("c1", 1.0), ("c2", 0.90), ("c3", 0.50)]
    selected = select_related_conversations(
        ranked, max_conversations=3, margin=0.85
    )
    assert selected == ["c1", "c2"]


def test_select_related_empty() -> None:
    assert select_related_conversations([], max_conversations=1, margin=0.85) == []


def test_best_hit_for_conversation() -> None:
    a = uuid.uuid4()
    b = uuid.uuid4()
    hits = [
        MessageHit(a, "conv-a", None, 0.01),
        MessageHit(b, "conv-a", None, 0.03),
        MessageHit(uuid.uuid4(), "conv-b", None, 0.02),
    ]
    best = best_hit_for_conversation(hits, "conv-a")
    assert best is not None
    assert best.embedding_id == b
    assert best_hit_for_conversation(hits, "missing") is None
