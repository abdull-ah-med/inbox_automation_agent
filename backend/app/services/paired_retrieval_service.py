"""Paired retrieval service — scope-walked cosine search for preference pairs + atoms.

Walks the scope ladder narrowest-to-widest and returns:
  - preference_pairs   matching the incoming email embedding per scope
  - fix_atoms          active Fix atoms for each scope
  - notes              active TeachingNotes for each scope

Scope walk order: sender_address → sender_domain →
  mailbox+routing_category → mailbox → global

Cross-mailbox safety: pairs from scopes other than "global" are always filtered
to the requesting mailbox.  Global-scope pairs are returned for all mailboxes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.scope_keys import scope_key_for
from app.models.db.feedback_atom import FeedbackAtom
from app.models.db.preference_pair import PreferencePair
from app.models.db.teaching_note import TeachingNote
from app.repositories._vector_common import set_hnsw_session_defaults
from app.repositories.feedback_atom_repo import FeedbackAtomSchema
from app.repositories.preference_pair_repo import PreferencePairSchema
from app.repositories.teaching_note_repo import TeachingNoteSchema


@dataclass
class RetrievedPairs:
    """Result of a paired retrieval pass for a single draft/email."""

    pairs: list[PreferencePairSchema] = field(default_factory=list)
    fix_atoms: list[FeedbackAtomSchema] = field(default_factory=list)
    spec_atoms: list[FeedbackAtomSchema] = field(default_factory=list)
    notes: list[TeachingNoteSchema] = field(default_factory=list)
    pair_scope_sources: list[str] = field(default_factory=list)


# Ordered narrowest → widest (global last so we always search it)
_SCOPE_ORDER = (
    "sender_address",
    "sender_domain",
    "mailbox+routing_category",
    "mailbox",
    "global",
)


def _pair_column_filters(
    scope: str,
    *,
    mailbox: str,
    sender_address: str | None,
    sender_domain: str | None,
    routing_category: str | None,
) -> list:
    """WHERE clauses that walk existing pair columns (no scope column on pairs)."""
    filters = [PreferencePair.mailbox == mailbox]
    if scope == "global":
        return []  # pairs have no global column; atoms/notes handle org-wide scope
    if scope == "sender_address":
        filters.append(PreferencePair.sender_address == (sender_address or "").strip().lower())
    elif scope == "sender_domain":
        filters.append(PreferencePair.sender_domain == (sender_domain or "").strip().lower())
    elif scope == "mailbox+routing_category":
        filters.append(PreferencePair.routing_category == routing_category)
    return filters


async def retrieve_for_draft(
    session: AsyncSession,
    *,
    mailbox: str,
    sender_address: str | None,
    sender_domain: str | None,
    routing_category: str | None,
    email_embedding: list[float],
    limit_per_scope: int = 2,
    hard_limit: int = 5,
) -> RetrievedPairs:
    """Walk the scope ladder and return pairs, fix atoms, and notes.

    Args:
        session: async DB session.
        mailbox: the monitored mailbox (strict allowlist filter for non-global).
        sender_address: normalised sender email (may be None).
        sender_domain: sender domain (may be None).
        routing_category: routing category label (may be None).
        email_embedding: 1536-dim embedding of the incoming email.
        limit_per_scope: max preference pairs per scope level.
        hard_limit: overall ceiling on returned pairs.

    Returns:
        RetrievedPairs with deduplicated pairs, fix_atoms, and notes.
    """
    settings = get_settings()
    await set_hnsw_session_defaults(session, settings)

    seen_pair_ids: set = set()
    seen_atom_ids: set = set()
    seen_note_ids: set = set()

    all_pairs: list[PreferencePairSchema] = []
    all_pair_scopes: list[str] = []
    all_atoms: list[FeedbackAtomSchema] = []
    all_spec_atoms: list[FeedbackAtomSchema] = []
    all_notes: list[TeachingNoteSchema] = []

    for scope in _SCOPE_ORDER:
        # Build scope_key for this level (skip if required context is missing)
        try:
            sk = scope_key_for(
                scope,
                sender_address=sender_address,
                sender_domain=sender_domain,
                mailbox=mailbox,
                routing_category=routing_category,
            )
        except ValueError:
            continue  # required context missing for this scope → skip

        # --- Preference pairs (cosine ANN). Stop further pair queries when full;
        # atoms and notes still walk the rest of the ladder. ---
        if len(all_pairs) < hard_limit and scope != "global":
            remaining = hard_limit - len(all_pairs)
            per_scope_limit = min(limit_per_scope, remaining)

            distance = PreferencePair.email_embedding.cosine_distance(email_embedding)
            pair_stmt = (
                select(PreferencePair, distance.label("distance"))
                .where(
                    *_pair_column_filters(
                        scope,
                        mailbox=mailbox,
                        sender_address=sender_address,
                        sender_domain=sender_domain,
                        routing_category=routing_category,
                    )
                )
                .order_by(distance)
                .limit(per_scope_limit)
            )

            pair_result = await session.execute(pair_stmt)
            for row in pair_result.all():
                pair = row[0]
                if pair.id in seen_pair_ids:
                    continue
                seen_pair_ids.add(pair.id)
                all_pairs.append(PreferencePairSchema.model_validate(pair))
                all_pair_scopes.append(scope)

        atom_distance = FeedbackAtom.atom_embedding.cosine_distance(email_embedding)
        atom_filters = [
            FeedbackAtom.scope == scope,
            FeedbackAtom.scope_key == sk,
            FeedbackAtom.role.in_(["Fix", "Spec"]),
            FeedbackAtom.is_active.is_(True),
        ]
        if scope == "global":
            atom_filters.append(FeedbackAtom.person_bound.is_(False))
        else:
            atom_filters.append(FeedbackAtom.mailbox == mailbox)
        atom_stmt = select(FeedbackAtom).where(*atom_filters).order_by(atom_distance).limit(10)
        atom_result = await session.execute(atom_stmt)
        for atom in atom_result.scalars().all():
            if atom.id in seen_atom_ids:
                continue
            seen_atom_ids.add(atom.id)
            if atom.role == "Spec":
                all_spec_atoms.append(FeedbackAtomSchema.model_validate(atom))
            else:
                all_atoms.append(FeedbackAtomSchema.model_validate(atom))

        # --- Teaching notes ---
        note_filters = [
            TeachingNote.scope == scope,
            TeachingNote.scope_key == sk,
            TeachingNote.status == "active",
        ]
        if scope == "global":
            note_filters.append(TeachingNote.person_bound.is_(False))
        else:
            note_filters.append(TeachingNote.mailbox == mailbox)
        note_stmt = (
            select(TeachingNote)
            .where(*note_filters)
            .order_by(TeachingNote.created_at.desc())
            .limit(5)
        )
        note_result = await session.execute(note_stmt)
        for note in note_result.scalars().all():
            if note.id in seen_note_ids:
                continue
            seen_note_ids.add(note.id)
            all_notes.append(TeachingNoteSchema.model_validate(note))

    return RetrievedPairs(
        pairs=all_pairs,
        fix_atoms=all_atoms,
        spec_atoms=all_spec_atoms,
        notes=all_notes,
        pair_scope_sources=all_pair_scopes,
    )
