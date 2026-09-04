"""Paired-retrieval context loading for draft generation.

Provides a flag check and a helper that embeds the incoming email, runs
the scope-walked retrieval, gates atoms/notes through the applies-when
gate, and returns Fix constraints plus ICDPO pair blocks.

Both the pipeline and the regeneration service call this; they only need
to import ``paired_retrieval_enabled`` and ``load_paired_constraints``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services import applies_when_gate, embedding_service, rejection_memory_service
from app.services.paired_retrieval_service import retrieve_for_draft

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PairedExampleBlock:
    chosen: str | None
    rejected: str | None
    scope_source: str


@dataclass(frozen=True)
class PairedDraftContext:
    fix_constraints: list[str] = field(default_factory=list)
    pair_blocks: list[PairedExampleBlock] = field(default_factory=list)
    atom_ids: list[uuid.UUID] = field(default_factory=list)
    note_ids: list[uuid.UUID] = field(default_factory=list)
    fix_atom_payloads: list[dict] = field(default_factory=list)


def paired_retrieval_enabled(settings: Settings, mailbox: str) -> bool:
    """Return True when *mailbox* is in the ``paired_retrieval_mailboxes`` CSV."""
    allow = [m.strip().lower() for m in settings.paired_retrieval_mailboxes.split(",") if m.strip()]
    return mailbox.lower() in allow


def _empty_context() -> PairedDraftContext:
    return PairedDraftContext()


async def load_legacy_negative_constraints(
    session: AsyncSession,
    *,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    mailbox: str,
    email_text: str,
    routing_category: str,
    limit: int = 3,
) -> list[str]:
    """Load the old rejection_memories pool, or skip it for flagged mailboxes.

    Phase 2: dual-write continues, but flagged mailboxes read ICDPO pairs
    instead of the rejection pool.
    """
    if paired_retrieval_enabled(settings, mailbox):
        return []
    return await rejection_memory_service.find_negative_constraints(
        session,
        openai_client=openai_client,
        settings=settings,
        email_text=email_text,
        mailbox=mailbox,
        routing_category=routing_category,
        limit=limit,
    )


async def load_paired_constraints(
    session: AsyncSession,
    *,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    anthropic_client: AsyncAnthropic | None,
    mailbox: str,
    email_text: str,
    sender_address: str | None,
    sender_domain: str | None,
    routing_category: str | None,
) -> PairedDraftContext:
    """Load Fix-atom + teaching-note constraints and ICDPO pair blocks.

    Returns an empty context immediately when:
    - paired retrieval is not enabled for *mailbox*, or
    - openai_client is None (cannot embed).

    Best-effort: any retrieval or gate failure returns an empty context.
    """
    if not paired_retrieval_enabled(settings, mailbox):
        return _empty_context()

    if openai_client is None or not settings.openai_api_key.strip():
        return _empty_context()

    try:
        email_embedding = await embedding_service.embed_text(
            email_text,
            client=openai_client,
            settings=settings,
        )
    except Exception:
        logger.exception("paired_retrieval_embed_failed", mailbox=mailbox)
        return _empty_context()

    try:
        retrieved = await retrieve_for_draft(
            session,
            mailbox=mailbox,
            sender_address=sender_address,
            sender_domain=sender_domain,
            routing_category=routing_category,
            email_embedding=email_embedding,
        )
    except Exception:
        logger.exception("paired_retrieval_retrieve_failed", mailbox=mailbox)
        return _empty_context()

    fix_n = len(retrieved.fix_atoms)
    spec_n = len(retrieved.spec_atoms)
    note_n = len(retrieved.notes)
    combined_conditions: list[str | None] = (
        [a.applies_when for a in retrieved.fix_atoms]
        + [a.applies_when for a in retrieved.spec_atoms]
        + [n.applies_when for n in retrieved.notes]
    )

    if anthropic_client is not None:
        try:
            combined_mask = await applies_when_gate.gate_atoms_and_notes(
                anthropic_client,
                settings,
                email_text,
                combined_conditions,
            )
        except Exception:
            logger.exception("paired_retrieval_gate_failed", mailbox=mailbox)
            combined_mask = [c is None for c in combined_conditions]
    else:
        combined_mask = [True] * len(combined_conditions)

    atom_mask = combined_mask[:fix_n]
    spec_mask = combined_mask[fix_n : fix_n + spec_n]
    note_mask = combined_mask[fix_n + spec_n : fix_n + spec_n + note_n]

    kept_atoms = [a for a, keep in zip(retrieved.fix_atoms, atom_mask, strict=False) if keep]
    kept_specs = [a for a, keep in zip(retrieved.spec_atoms, spec_mask, strict=False) if keep]
    kept_notes = [n for n, keep in zip(retrieved.notes, note_mask, strict=False) if keep]

    constraint_lines: list[str] = []
    for atom in kept_atoms:
        line = atom.atom_text
        if atom.applies_when:
            line = f"{line} (when: {atom.applies_when})"
        constraint_lines.append(line)
    for atom in kept_specs:
        line = f"Must satisfy: {atom.atom_text}"
        if atom.applies_when:
            line = f"{line} (when: {atom.applies_when})"
        constraint_lines.append(line)
    for note in kept_notes:
        constraint_lines.append(note.body)

    atom_ids = [a.id for a in kept_atoms] + [a.id for a in kept_specs]
    note_ids = [n.id for n in kept_notes]
    fix_atom_payloads = [
        {
            "id": str(a.id),
            "atom_text": a.atom_text,
            "applies_when": a.applies_when,
        }
        for a in kept_atoms
    ]

    scopes = retrieved.pair_scope_sources
    pair_blocks: list[PairedExampleBlock] = []
    for idx, pair in enumerate(retrieved.pairs):
        chosen = getattr(pair, "chosen_body", None)
        rejected = getattr(pair, "rejected_body", None)
        if not chosen and not rejected:
            continue
        scope_source = scopes[idx] if idx < len(scopes) else "mailbox"
        pair_blocks.append(
            PairedExampleBlock(
                chosen=chosen,
                rejected=rejected,
                scope_source=scope_source,
            )
        )

    logger.info(
        "paired_retrieve",
        mailbox=mailbox,
        pair_count=len(retrieved.pairs),
        atom_count=len(kept_atoms),
        note_count=len(kept_notes),
    )

    return PairedDraftContext(
        fix_constraints=constraint_lines,
        pair_blocks=pair_blocks,
        atom_ids=atom_ids,
        note_ids=note_ids,
        fix_atom_payloads=fix_atom_payloads,
    )
