"""Reciprocal Rank Fusion + conversation aggregation for hybrid retrieval.

Worked example (rrf_k=60, corroboration_bonus=0.15, max_hits=3):

Conversation A — one hit at vector-leg rank 2:
  top_score(A) = 1/(60+2) = 0.016129
  extra_hits = 0 → conversation_score(A) = 0.016129

Conversation B — three hits at ranks 4, 6, 9 (vector leg only):
  top_score(B) = 1/(60+4) = 0.015625
  extra_hits = 2 → conversation_score(B) = 0.015625 * (1 + 0.15*2) = 0.020313

B outranks A: three corroborating hits beat one moderately-stronger single hit.

v1 wires only the top conversation into the prompt
(``embedding_final_conversations=1``). Soft-margin selection already supports
N>1 so raising the config later does not require rewriting retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class MessageHit:
    embedding_id: UUID
    conversation_id: str
    message_id: UUID | None
    rrf_score: float


def rrf_fuse(
    vector_hits: list[tuple[UUID, str, UUID | None]],
    fts_hits: list[tuple[UUID, str, UUID | None]],
    keyword_hits: list[tuple[UUID, str, UUID | None]] | None = None,
    *,
    k: int = 60,
) -> list[MessageHit]:
    """Fuse ranked lists with classic RRF (Cormack et al. 2009).

    Each input list is ordered best-first. Tuple = (embedding_id, conversation_id, message_id).
    """
    scores: dict[UUID, float] = {}
    meta: dict[UUID, tuple[str, UUID | None]] = {}

    for rank, (emb_id, conv_id, msg_id) in enumerate(vector_hits, start=1):
        scores[emb_id] = scores.get(emb_id, 0.0) + 1.0 / (k + rank)
        meta[emb_id] = (conv_id, msg_id)

    later_legs = [fts_hits]
    if keyword_hits:
        later_legs.append(keyword_hits)
    for hits in later_legs:
        for rank, (emb_id, conv_id, msg_id) in enumerate(hits, start=1):
            scores[emb_id] = scores.get(emb_id, 0.0) + 1.0 / (k + rank)
            meta.setdefault(emb_id, (conv_id, msg_id))

    fused = [
        MessageHit(
            embedding_id=emb_id,
            conversation_id=meta[emb_id][0],
            message_id=meta[emb_id][1],
            rrf_score=score,
        )
        for emb_id, score in scores.items()
    ]
    fused.sort(key=lambda h: h.rrf_score, reverse=True)
    return fused


def aggregate_conversation_scores(
    hits: list[MessageHit],
    *,
    bonus: float = 0.15,
    max_bonus_hits: int = 3,
) -> dict[str, float]:
    """Max single-hit RRF score, boosted by corroborating hits in the same conversation."""
    by_conv: dict[str, list[float]] = {}
    for hit in hits:
        by_conv.setdefault(hit.conversation_id, []).append(hit.rrf_score)

    scores: dict[str, float] = {}
    for conv_id, vals in by_conv.items():
        top = max(vals)
        extra = max(0, len(vals) - 1)
        capped_extra = min(extra, max_bonus_hits)
        scores[conv_id] = top * (1.0 + bonus * capped_extra)
    return scores


def select_related_conversations(
    ranked: list[tuple[str, float]],
    *,
    max_conversations: int,
    margin: float,
) -> list[str]:
    """Soft-margin selection over (conversation_id, score) sorted descending.

    Always keeps #1. Keeps subsequent only while score >= margin * top_score,
    up to ``max_conversations``. With max_conversations=1 this is a hard top-1.
    """
    if not ranked:
        return []
    selected = [ranked[0][0]]
    top_score = ranked[0][1]
    for conversation_id, score in ranked[1:]:
        if len(selected) >= max_conversations:
            break
        if score >= margin * top_score:
            selected.append(conversation_id)
    return selected


def best_hit_for_conversation(
    hits: list[MessageHit],
    conversation_id: str,
) -> MessageHit | None:
    matching = [h for h in hits if h.conversation_id == conversation_id]
    if not matching:
        return None
    return max(matching, key=lambda h: h.rrf_score)
