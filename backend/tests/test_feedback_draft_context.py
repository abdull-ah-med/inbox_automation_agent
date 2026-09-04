"""Tests for feedback_draft_context.py.

Oracle: plan §B spec — mailbox not in CSV returns empty; mailbox in CSV
with a seeded pair returns at least one constraint.

Mock strategy:
- embed_text → vendor boundary (OpenAI); returns a fixed 1536-dim vector.
- retrieve_for_draft → real call (but DB seeded with atoms).
- gate_atoms_and_notes → Anthropic boundary; mocked to return all-True.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.services.feedback_draft_context import (
    load_paired_constraints,
    paired_retrieval_enabled,
)

# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

MAILBOX = "sales@example.com"
OTHER_MAILBOX = "support@example.com"


def _settings(*, extra_mailbox: str = "") -> Settings:
    csv = MAILBOX if not extra_mailbox else f"{MAILBOX},{extra_mailbox}"
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        openai_api_key="sk-test",
        paired_retrieval_mailboxes=csv,
        feedback_atoms_enabled=True,
    )


# ---------------------------------------------------------------------------
# Unit tests for paired_retrieval_enabled
# ---------------------------------------------------------------------------


def test_paired_retrieval_enabled_mailbox_in_csv() -> None:
    settings = _settings()
    assert paired_retrieval_enabled(settings, MAILBOX) is True


def test_paired_retrieval_enabled_case_insensitive() -> None:
    settings = _settings()
    assert paired_retrieval_enabled(settings, MAILBOX.upper()) is True


def test_paired_retrieval_disabled_mailbox_not_in_csv() -> None:
    settings = _settings()
    # OTHER_MAILBOX is not in the CSV
    assert paired_retrieval_enabled(settings, OTHER_MAILBOX) is False


def test_paired_retrieval_disabled_empty_csv() -> None:
    settings = Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        paired_retrieval_mailboxes="",
    )
    assert paired_retrieval_enabled(settings, MAILBOX) is False


# ---------------------------------------------------------------------------
# load_paired_constraints: mailbox not enabled → ([], [], [])
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_paired_constraints_mailbox_not_enabled() -> None:
    """Mailbox not in CSV: empty result without any DB or LLM calls."""
    settings = _settings()
    fake_session = MagicMock()
    fake_openai = MagicMock()
    fake_anthropic = MagicMock()

    ctx = await load_paired_constraints(
        fake_session,
        settings=settings,
        openai_client=fake_openai,
        anthropic_client=fake_anthropic,
        mailbox=OTHER_MAILBOX,  # not in CSV
        email_text="Need invoice copy",
        sender_address="vendor@acme.com",
        sender_domain="acme.com",
        routing_category="billing",
    )

    assert ctx.fix_constraints == []
    assert ctx.atom_ids == []
    assert ctx.note_ids == []
    assert ctx.pair_blocks == []
    # No embedding call should have been made
    fake_openai.embeddings.create.assert_not_called()


@pytest.mark.asyncio
async def test_load_paired_constraints_no_openai_returns_empty() -> None:
    """Without an OpenAI client, embedding is impossible → empty result."""
    settings = _settings()
    fake_session = MagicMock()

    ctx = await load_paired_constraints(
        fake_session,
        settings=settings,
        openai_client=None,
        anthropic_client=MagicMock(),
        mailbox=MAILBOX,
        email_text="Need invoice copy",
        sender_address=None,
        sender_domain=None,
        routing_category=None,
    )

    assert ctx.fix_constraints == []
    assert ctx.atom_ids == []
    assert ctx.note_ids == []
    assert ctx.pair_blocks == []


# ---------------------------------------------------------------------------
# load_paired_constraints: mailbox enabled, seeded atom → constraint returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_paired_constraints_returns_constraints_from_seeded_atom() -> None:
    """Mailbox in CSV + seeded Fix atom → at least one constraint string."""
    settings = _settings()
    fake_session = MagicMock()
    fake_openai = MagicMock()

    atom_id = uuid.uuid4()
    fake_atom = MagicMock()
    fake_atom.id = atom_id
    fake_atom.atom_text = "Always acknowledge the driver name"
    fake_atom.applies_when = None

    fake_note = MagicMock()
    fake_note.id = uuid.uuid4()
    fake_note.body = "Cc billing on large invoices"
    fake_note.applies_when = None

    from app.services.paired_retrieval_service import RetrievedPairs

    fake_retrieved = RetrievedPairs(
        pairs=[],
        fix_atoms=[fake_atom],
        notes=[fake_note],
    )

    with (
        patch(
            "app.services.feedback_draft_context.embedding_service.embed_text",
            AsyncMock(return_value=[0.1] * 1536),
        ),
        patch(
            "app.services.feedback_draft_context.retrieve_for_draft",
            AsyncMock(return_value=fake_retrieved),
        ),
        patch(
            "app.services.feedback_draft_context.applies_when_gate.gate_atoms_and_notes",
            AsyncMock(return_value=[True, True]),
        ),
    ):
        ctx = await load_paired_constraints(
            fake_session,
            settings=settings,
            openai_client=fake_openai,
            anthropic_client=MagicMock(),
            mailbox=MAILBOX,
            email_text="Invoice for March",
            sender_address="vendor@acme.com",
            sender_domain="acme.com",
            routing_category="billing",
        )

    # At least one constraint from the seeded Fix atom
    assert len(ctx.fix_constraints) >= 1
    assert any("driver name" in c for c in ctx.fix_constraints)
    assert atom_id in ctx.atom_ids
    assert len(ctx.note_ids) >= 1


@pytest.mark.asyncio
async def test_load_paired_constraints_gated_out_atoms_excluded() -> None:
    """Atoms where the gate returns False are excluded from constraints."""
    settings = _settings()
    fake_session = MagicMock()

    atom_id = uuid.uuid4()
    fake_atom = MagicMock()
    fake_atom.id = atom_id
    fake_atom.atom_text = "Only when driver mentioned"
    fake_atom.applies_when = "when driver name is in the subject"

    from app.services.paired_retrieval_service import RetrievedPairs

    fake_retrieved = RetrievedPairs(pairs=[], fix_atoms=[fake_atom], notes=[])

    with (
        patch(
            "app.services.feedback_draft_context.embedding_service.embed_text",
            AsyncMock(return_value=[0.0] * 1536),
        ),
        patch(
            "app.services.feedback_draft_context.retrieve_for_draft",
            AsyncMock(return_value=fake_retrieved),
        ),
        patch(
            "app.services.feedback_draft_context.applies_when_gate.gate_atoms_and_notes",
            AsyncMock(return_value=[False]),  # gated OUT
        ),
    ):
        ctx = await load_paired_constraints(
            fake_session,
            settings=settings,
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            mailbox=MAILBOX,
            email_text="Routine update",
            sender_address=None,
            sender_domain=None,
            routing_category=None,
        )

    # Atom was gated out → no constraints, no ids
    assert ctx.fix_constraints == []
    assert ctx.atom_ids == []


@pytest.mark.asyncio
async def test_load_paired_constraints_returns_pair_blocks_not_in_constraints() -> None:
    """ICDPO pair blocks are a separate list; chosen/rejected are not Fix constraints."""
    settings = _settings()
    fake_session = MagicMock()

    fake_pair = MagicMock()
    fake_pair.id = uuid.uuid4()
    fake_pair.chosen_body = "Thanks — POD is attached."
    fake_pair.rejected_body = "We cannot help with this."
    fake_pair.sender_address = "vendor@acme.com"

    fake_atom = MagicMock()
    fake_atom.id = uuid.uuid4()
    fake_atom.atom_text = "Always greet by first name"
    fake_atom.applies_when = None

    from app.services.paired_retrieval_service import RetrievedPairs

    fake_retrieved = RetrievedPairs(
        pairs=[fake_pair],
        fix_atoms=[fake_atom],
        notes=[],
    )
    # Retrieval tags the pair with the scope it matched (sender_address).
    fake_retrieved.pair_scope_sources = ["sender_address"]

    with (
        patch(
            "app.services.feedback_draft_context.embedding_service.embed_text",
            AsyncMock(return_value=[0.1] * 1536),
        ),
        patch(
            "app.services.feedback_draft_context.retrieve_for_draft",
            AsyncMock(return_value=fake_retrieved),
        ),
        patch(
            "app.services.feedback_draft_context.applies_when_gate.gate_atoms_and_notes",
            AsyncMock(return_value=[True]),
        ),
    ):
        ctx = await load_paired_constraints(
            fake_session,
            settings=settings,
            openai_client=MagicMock(),
            anthropic_client=MagicMock(),
            mailbox=MAILBOX,
            email_text="Need the POD",
            sender_address="vendor@acme.com",
            sender_domain="acme.com",
            routing_category="billing",
        )

    assert any("first name" in line for line in ctx.fix_constraints)
    assert "Thanks — POD is attached." not in ctx.fix_constraints
    assert "We cannot help with this." not in ctx.fix_constraints
    assert len(ctx.pair_blocks) == 1
    block = ctx.pair_blocks[0]
    assert block.chosen == "Thanks — POD is attached."
    assert block.rejected == "We cannot help with this."
    assert block.scope_source == "sender_address"


@pytest.mark.asyncio
async def test_skip_legacy_rejection_pool_when_paired_retrieval_enabled() -> None:
    """Phase 2: flagged mailboxes must not read rejection_memories."""
    from app.services.feedback_draft_context import load_legacy_negative_constraints

    settings = _settings()
    boom = AsyncMock(side_effect=AssertionError("legacy pool must not be read"))
    with patch(
        "app.services.rejection_memory_service.find_negative_constraints",
        boom,
    ):
        result = await load_legacy_negative_constraints(
            MagicMock(),
            settings=settings,
            openai_client=MagicMock(),
            mailbox=MAILBOX,
            email_text="Need invoice copy",
            routing_category="billing",
        )
    assert result == []


@pytest.mark.asyncio
async def test_legacy_rejection_pool_read_when_mailbox_not_flagged() -> None:
    """Unflagged mailboxes still load the old rejection pool."""
    from app.services.feedback_draft_context import load_legacy_negative_constraints

    settings = _settings()
    with patch(
        "app.services.rejection_memory_service.find_negative_constraints",
        AsyncMock(return_value=["Do not promise an SLA"]),
    ):
        result = await load_legacy_negative_constraints(
            MagicMock(),
            settings=settings,
            openai_client=MagicMock(),
            mailbox=OTHER_MAILBOX,
            email_text="Need invoice copy",
            routing_category="billing",
        )
    assert result == ["Do not promise an SLA"]
