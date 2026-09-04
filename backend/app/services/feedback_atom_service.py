"""Feedback atom service — SLIFT decomposition, scope routing, persistence.

Public API
----------
default_scope_from(...)      -> (scope, scope_key, person_bound)
atomize_and_persist(...)     -> list[FeedbackAtomSchema]   (best-effort)
record_hit(session, atom_id)
record_outcome(session, draft_id, was_approved)
propose_widenings(session, mailbox)   -> list[PromotionProposalSchema]
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.core.scope_keys import resolve_widening_scope, scope_key_for
from app.llm.atom_extract import extract_atoms
from app.models.db.feedback_atom import FeedbackAtom
from app.repositories import draft_repo, feedback_atom_repo, promotion_proposal_repo
from app.services import embedding_service

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Scope ladder constants (plan §Critical locks)
# ---------------------------------------------------------------------------
_SCOPE_LADDER = (
    "thread",
    "sender_address",
    "sender_domain",
    "mailbox+routing_category",
    "mailbox",
    # "global" is intentionally absent — never returned by default_scope_from
)

_APPROVAL_SCOPE_MAP = {
    "once": "thread",
    "similar": "mailbox+routing_category",
    "sender_address": "sender_address",
    "mailbox": "mailbox",
}

_PERSON_BOUND_MARKERS = (
    "only elise",
    "elise only",
    "my calendar",
    "i personally",
    "personal relationship",
    "my tone",
    "sign off with my",
    "only i ",
)


def default_scope_from(
    _atom_text: str,
    suggested_scope: str,
    *,
    thread_id: object,
    sender_address: str | None,
    sender_domain: str | None,
    mailbox: str,
    routing_category: str | None,
    approval_scope: str | None = None,
) -> tuple[str, str, bool]:
    """Map a Haiku-suggested scope to a concrete (scope, scope_key, person_bound).

    Critical invariant: NEVER returns scope == "global".  When suggested_scope is
    "global" (or any unrecognised value), falls back to "mailbox".
    When the reviewer picked an approval_scope, that value wins over Haiku.
    """
    if approval_scope in _APPROVAL_SCOPE_MAP:
        scope = _APPROVAL_SCOPE_MAP[approval_scope]
    else:
        scope = "mailbox" if suggested_scope not in _SCOPE_LADDER else suggested_scope

    while True:
        try:
            key = scope_key_for(
                scope,
                thread_id=thread_id,
                sender_address=sender_address,
                sender_domain=sender_domain,
                mailbox=mailbox,
                routing_category=routing_category,
            )
            break
        except ValueError:
            if scope == "mailbox":
                key = scope_key_for("mailbox", mailbox=mailbox)
                break
            scope = _widen(scope, _mailbox=mailbox)

    lowered = (_atom_text or "").lower()
    content_bound = any(marker in lowered for marker in _PERSON_BOUND_MARKERS)
    person_bound = scope in {"thread", "sender_address"} or content_bound
    return scope, key, person_bound


def _widen(scope: str, *, _mailbox: str) -> str:
    """Return the next wider scope, bottoming out at mailbox (never global)."""
    ladder = list(_SCOPE_LADDER)
    try:
        idx = ladder.index(scope)
    except ValueError:
        return "mailbox"
    # Move one step toward mailbox; clamp at last (mailbox)
    return ladder[min(idx + 1, len(ladder) - 1)]


# ---------------------------------------------------------------------------
# atomize_and_persist
# ---------------------------------------------------------------------------


async def atomize_and_persist(
    session: AsyncSession,
    client: AsyncAnthropic,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    *,
    source_kind: str,
    source_id: uuid.UUID,
    mailbox: str,
    text: str,
    thread_id: uuid.UUID,
    sender_address: str | None,
    sender_domain: str | None,
    routing_category: str | None,
    email_text_for_context: str | None = None,
    draft_body: str | None = None,
    approval_scope: str | None = None,
) -> list:
    """Decompose *text* into atoms, embed, and persist each one.

    Best-effort: skips when ``feedback_atoms_enabled`` is False or when
    required clients are missing. Never raises to the caller. Returns the list
    of persisted FeedbackAtomSchema objects (empty on any skip or failure).
    """
    if not settings.feedback_atoms_enabled:
        return []
    if not text.strip():
        return []

    try:
        raw_atoms = await extract_atoms(
            client,
            settings,
            feedback_text=text,
            email_text_for_context=email_text_for_context,
            draft_body=draft_body,
        )
    except Exception:
        logger.exception(
            "atomize_extract_failed",
            source_kind=source_kind,
            source_id=str(source_id),
        )
        return []

    persisted = []
    for atom_dict in raw_atoms:
        role = atom_dict.get("role", "Null")
        if role == "Null":
            continue  # Null atoms are not persisted

        atom_text = atom_dict.get("text", "").strip()
        if not atom_text:
            continue

        suggested_scope = atom_dict.get("suggested_scope", "mailbox")
        applies_when = atom_dict.get("applies_when")

        scope, scope_key, person_bound = default_scope_from(
            atom_text,
            suggested_scope,
            thread_id=thread_id,
            sender_address=sender_address,
            sender_domain=sender_domain,
            mailbox=mailbox,
            routing_category=routing_category,
            approval_scope=approval_scope,
        )

        # Embed the atom text
        if openai_client is None or not settings.openai_api_key.strip():
            logger.info(
                "atomize_embed_skipped_no_openai",
                mailbox=mailbox,
            )
            continue

        try:
            embedding = await embedding_service.embed_text(
                atom_text,
                client=openai_client,
                settings=settings,
            )
        except Exception:
            logger.exception(
                "atomize_embed_failed",
                mailbox=mailbox,
            )
            continue

        try:
            stored = await feedback_atom_repo.insert_atom(
                session,
                source_kind=source_kind,
                source_id=source_id,
                mailbox=mailbox,
                atom_text=atom_text,
                atom_embedding=embedding,
                role=role,
                scope=scope,
                scope_key=scope_key,
                applies_when=applies_when,
                person_bound=person_bound,
            )
            persisted.append(stored)
            logger.info(
                "atom_persisted",
                atom_id=str(stored.id),
                role=role,
                scope=scope,
                mailbox=mailbox,
            )
        except Exception:
            logger.exception(
                "atomize_persist_failed",
                mailbox=mailbox,
            )

    return persisted


# ---------------------------------------------------------------------------
# record_hit / record_outcome
# ---------------------------------------------------------------------------


async def record_hit(session: AsyncSession, atom_id: uuid.UUID) -> None:
    """Increment hit_count for the given atom (best-effort)."""
    try:
        stmt = (
            sa_update(FeedbackAtom)
            .where(FeedbackAtom.id == atom_id)
            .values(hit_count=FeedbackAtom.hit_count + 1)
        )
        await session.execute(stmt)
    except Exception:
        logger.exception("record_hit_failed", atom_id=str(atom_id))


async def record_retrieval_hits(
    session: AsyncSession,
    *,
    atom_ids: list[uuid.UUID] | None,
    note_ids: list[uuid.UUID] | None,
) -> None:
    """Increment hits for atoms/notes after a draft row is persisted."""
    from app.repositories import teaching_note_repo

    for atom_id in atom_ids or []:
        await record_hit(session, atom_id)
    for note_id in note_ids or []:
        try:
            await teaching_note_repo.increment_hit_count(session, note_id)
        except Exception:
            logger.exception("record_note_hit_failed", note_id=str(note_id))


async def record_outcome(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    was_approved: bool,
) -> None:
    """Update precision counters on atoms and teaching notes retrieved for *draft_id*.

    Looks up ``Draft.retrieved_atom_ids`` and ``Draft.retrieved_note_ids`` to
    find which atoms and notes were served, then increments:
      - precision_den += 1 (denominator for all retrieved items)
      - precision_num += 1 when was_approved (numerator for correct predictions)

    Best-effort: never raises. No-ops when draft has no retrieved ids.
    """
    try:
        draft = await draft_repo.get_draft_by_id(session, draft_id)
        if draft is None:
            return

        atom_ids: list[uuid.UUID] = draft.retrieved_atom_ids or []
        note_ids: list[uuid.UUID] = draft.retrieved_note_ids or []

        if atom_ids:
            delta_num = 1 if was_approved else 0
            stmt = (
                sa_update(FeedbackAtom)
                .where(FeedbackAtom.id.in_(atom_ids))
                .values(
                    precision_den=FeedbackAtom.precision_den + 1,
                    precision_num=FeedbackAtom.precision_num + delta_num,
                )
            )
            await session.execute(stmt)

        if note_ids:
            from app.models.db.teaching_note import TeachingNote

            delta_num = 1 if was_approved else 0
            stmt = (
                sa_update(TeachingNote)
                .where(TeachingNote.id.in_(note_ids))
                .values(
                    precision_den=TeachingNote.precision_den + 1,
                    precision_num=TeachingNote.precision_num + delta_num,
                )
            )
            await session.execute(stmt)

    except Exception:
        logger.exception(
            "record_outcome_failed",
            draft_id=str(draft_id),
            was_approved=was_approved,
        )


# ---------------------------------------------------------------------------
# propose_widenings
# ---------------------------------------------------------------------------

_NARROW_SCOPES = frozenset(
    {"thread", "sender_address", "sender_domain", "mailbox+routing_category"}
)
_MIN_WIDENING_CLUSTER = 3
_COSINE_DISTANCE_MAX = 0.15  # cosine similarity >= 0.85
_PROPOSAL_EXPIRY_DAYS = 30


async def propose_widenings(session: AsyncSession, mailbox: str) -> list:
    """Propose scope promotions when ≥3 similar Fix atoms share a narrow scope.

    Neighbors are found with pgvector ``cosine_distance`` in SQL (not Python
    O(n²)). Distance < 0.15 and cluster size ≥ 3 → one ``atom_widening`` card.
    """
    try:
        meta_stmt = select(
            FeedbackAtom.id,
            FeedbackAtom.scope,
            FeedbackAtom.scope_key,
            FeedbackAtom.person_bound,
        ).where(
            FeedbackAtom.mailbox == mailbox,
            FeedbackAtom.role == "Fix",
            FeedbackAtom.is_active.is_(True),
            FeedbackAtom.scope.in_(_NARROW_SCOPES),
        )
        metas = list((await session.execute(meta_stmt)).all())
    except Exception:
        logger.exception("propose_widenings_load_failed", mailbox=mailbox)
        return []

    if len(metas) < _MIN_WIDENING_CLUSTER:
        return []

    seed = aliased(FeedbackAtom)
    other = aliased(FeedbackAtom)
    distance = seed.atom_embedding.cosine_distance(other.atom_embedding)
    try:
        pair_stmt = select(seed.id, other.id).where(
            seed.mailbox == mailbox,
            other.mailbox == mailbox,
            seed.role == "Fix",
            other.role == "Fix",
            seed.is_active.is_(True),
            other.is_active.is_(True),
            seed.scope.in_(_NARROW_SCOPES),
            other.scope.in_(_NARROW_SCOPES),
            seed.id < other.id,
            distance < _COSINE_DISTANCE_MAX,
        )
        pairs = list((await session.execute(pair_stmt)).all())
    except Exception:
        logger.exception("propose_widenings_distance_failed", mailbox=mailbox)
        return []

    neighbors: dict[uuid.UUID, set[uuid.UUID]] = {row.id: set() for row in metas}
    for left_id, right_id in pairs:
        neighbors.setdefault(left_id, set()).add(right_id)
        neighbors.setdefault(right_id, set()).add(left_id)

    used: set[uuid.UUID] = set()
    proposals: list = []
    expires_at = datetime.now(UTC) + timedelta(days=_PROPOSAL_EXPIRY_DAYS)
    meta_by_id = {row.id: row for row in metas}

    for row in metas:
        seed_id = row.id
        if seed_id in used:
            continue
        cluster_ids = [seed_id]
        for nbr_id in neighbors.get(seed_id, ()):
            if nbr_id not in used:
                cluster_ids.append(nbr_id)
        if len(cluster_ids) < _MIN_WIDENING_CLUSTER:
            continue
        for member_id in cluster_ids:
            used.add(member_id)
        cluster_rows = [meta_by_id[i] for i in cluster_ids if i in meta_by_id]
        try:
            next_scope = _widen(row.scope, _mailbox=mailbox)
            resolved_scope, requested_key = resolve_widening_scope(
                next_scope,
                mailbox=mailbox,
                source_scope_key=row.scope_key,
            )
            proposal = await promotion_proposal_repo.create_promotion_proposal(
                session,
                mailbox=mailbox,
                kind="atom_widening",
                payload={
                    "source_kind": "feedback_atom",
                    "person_bound": any(m.person_bound for m in cluster_rows),
                    "from_scope": row.scope,
                    "requested_scope": resolved_scope,
                    "requested_scope_key": requested_key,
                    "atom_id": str(seed_id),
                },
                impact_num=len(cluster_ids),
                impact_den=len(cluster_ids),
                evidence_ids=cluster_ids,
                expires_at=expires_at,
            )
            proposals.append(proposal)
            logger.info(
                "atom_widening_proposal_created",
                mailbox=mailbox,
                count=len(cluster_ids),
            )
        except Exception:
            logger.warning("atom_widening_proposal_failed", mailbox=mailbox)

    return proposals
