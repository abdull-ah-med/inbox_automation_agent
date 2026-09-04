"""Tests for feedback_atom_service.

Oracle source: plan §10 + hand-labelled atom fixtures.

Mock strategy: Haiku client mocked at the AsyncAnthropic boundary only.
OpenAI client mocked at the AsyncOpenAI boundary only.
No DB — tests default_scope_from + atomize_and_persist early-exit paths.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.services.feedback_atom_service import default_scope_from

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MAILBOX = "ops@example.com"
THREAD_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")


def _make_settings(**kwargs) -> Settings:
    base = {
        "graph_client_id": "x",
        "graph_client_secret": "x",
        "graph_tenant_id": "x",
        "target_mailboxes": MAILBOX,
        "environment": "local",
        "database_url": "postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        "redis_url": "redis://localhost:6379/15",
        "openai_api_key": "sk-test",
    }
    base.update(kwargs)
    return Settings(**base)


# ---------------------------------------------------------------------------
# default_scope_from: role-independent invariants
# ---------------------------------------------------------------------------


def test_default_scope_from_never_returns_global_from_global() -> None:
    """Passing 'global' as suggested_scope NEVER produces scope='global'."""
    scope, key, _ = default_scope_from(
        "A generic rule",
        "global",
        thread_id=THREAD_ID,
        sender_address="a@b.com",
        sender_domain="b.com",
        mailbox=MAILBOX,
        routing_category="general",
    )
    assert scope != "global"
    assert key != "global:"


def test_default_scope_from_never_returns_global_from_garbage() -> None:
    """Garbage suggested_scope NEVER produces scope='global'."""
    for bad in ("organisation", "", "ALL", "Global", "GLOBAL"):
        scope, _key, _ = default_scope_from(
            "Any text",
            bad,
            thread_id=THREAD_ID,
            sender_address="x@y.com",
            sender_domain="y.com",
            mailbox=MAILBOX,
            routing_category="billing",
        )
        assert scope != "global", f"scope='global' returned for suggested_scope={bad!r}"


def test_person_bound_true_for_thread_scope() -> None:
    """Thread scope always sets person_bound=True (one-off habit)."""
    _, _, person_bound = default_scope_from(
        "Only this once, reply same-day",
        "thread",
        thread_id=THREAD_ID,
        sender_address="x@y.com",
        sender_domain="y.com",
        mailbox=MAILBOX,
        routing_category="general",
    )
    assert person_bound is True


def test_person_bound_true_for_sender_address_scope() -> None:
    """sender_address scope always sets person_bound=True (Elise's sender relationship)."""
    _, _, person_bound = default_scope_from(
        "Greet this sender by first name",
        "sender_address",
        thread_id=THREAD_ID,
        sender_address="elise@client.com",
        sender_domain="client.com",
        mailbox=MAILBOX,
        routing_category="general",
    )
    assert person_bound is True


def test_person_bound_false_for_domain_scope() -> None:
    """Domain scope is not person-specific."""
    _, _, person_bound = default_scope_from(
        "Reply within 24h to all acme.com emails",
        "sender_domain",
        thread_id=THREAD_ID,
        sender_address="a@acme.com",
        sender_domain="acme.com",
        mailbox=MAILBOX,
        routing_category="billing",
    )
    assert person_bound is False


def test_person_bound_false_for_mailbox_scope() -> None:
    """Mailbox scope is not person-specific."""
    _, _, person_bound = default_scope_from(
        "End every reply with the team signature",
        "mailbox",
        thread_id=THREAD_ID,
        sender_address="a@b.com",
        sender_domain="b.com",
        mailbox=MAILBOX,
        routing_category=None,
    )
    assert person_bound is False


def test_approval_scope_once_wins_over_haiku_mailbox() -> None:
    """Reviewer approval_scope=once maps to thread even when Haiku suggests mailbox."""
    scope, key, person_bound = default_scope_from(
        "Always include the invoice number",
        "mailbox",
        thread_id=THREAD_ID,
        sender_address="a@b.com",
        sender_domain="b.com",
        mailbox=MAILBOX,
        routing_category="billing",
        approval_scope="once",
    )
    assert scope == "thread"
    assert key == f"thread:{THREAD_ID}"
    assert person_bound is True


def test_content_markers_set_person_bound_on_mailbox_scope() -> None:
    """Phrases like 'my calendar' mark an otherwise mailbox-scoped atom person-bound."""
    _, _, person_bound = default_scope_from(
        "Never book on my calendar without asking",
        "mailbox",
        thread_id=THREAD_ID,
        sender_address="a@b.com",
        sender_domain="b.com",
        mailbox=MAILBOX,
        routing_category="general",
    )
    assert person_bound is True


# ---------------------------------------------------------------------------
# atomize_and_persist: flag-off skips without crashing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_atomize_skips_when_flag_off() -> None:
    """atomize_and_persist returns [] immediately when feedback_atoms_enabled=False."""
    from app.services.feedback_atom_service import atomize_and_persist

    settings = _make_settings(feedback_atoms_enabled=False)
    mock_session = AsyncMock()
    mock_anthropic = AsyncMock()
    mock_openai = AsyncMock()

    result = await atomize_and_persist(
        mock_session,
        mock_anthropic,
        settings,
        mock_openai,
        source_kind="rejection",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX,
        text="The tone was too formal",
        thread_id=THREAD_ID,
        sender_address="vendor@acme.com",
        sender_domain="acme.com",
        routing_category="billing",
    )
    assert result == []
    # Anthropic client must NOT be called when flag is off
    mock_anthropic.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_atomize_skips_empty_text() -> None:
    """atomize_and_persist returns [] immediately for empty/whitespace text."""
    from app.services.feedback_atom_service import atomize_and_persist

    settings = _make_settings(feedback_atoms_enabled=True)
    mock_session = AsyncMock()
    mock_anthropic = AsyncMock()
    mock_openai = AsyncMock()

    result = await atomize_and_persist(
        mock_session,
        mock_anthropic,
        settings,
        mock_openai,
        source_kind="rejection",
        source_id=uuid.uuid4(),
        mailbox=MAILBOX,
        text="   ",  # whitespace only
        thread_id=THREAD_ID,
        sender_address="a@b.com",
        sender_domain="b.com",
        routing_category="general",
    )
    assert result == []
    mock_anthropic.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_atomize_roles_fix_spec_null_from_mock_haiku() -> None:
    """Mock Haiku returns Fix + Spec + Null; only Fix and Spec are persisted."""
    from app.services.feedback_atom_service import atomize_and_persist

    settings = _make_settings(feedback_atoms_enabled=True)

    # Haiku mock returns three atoms: Fix, Spec, Null
    haiku_atoms_json = (
        '{"atoms":['
        '{"text":"Always address Elise by first name","role":"Fix","applies_when":null,'
        '"suggested_scope":"sender_address"},'
        '{"text":"When invoice amount exceeds $1000","role":"Spec",'
        '"applies_when":"invoice amount > $1000","suggested_scope":"mailbox+routing_category"},'
        '{"text":"Looks fine","role":"Null","applies_when":null,"suggested_scope":"mailbox"}'
        "]}"
    )
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=haiku_atoms_json)]

    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create = AsyncMock(return_value=mock_msg)

    # Embed mock — returns a unit vector
    mock_embed_response = MagicMock()
    mock_embed_response.data = [MagicMock(embedding=[0.1] * 1536)]
    mock_openai = AsyncMock()
    mock_openai.embeddings.create = AsyncMock(return_value=mock_embed_response)

    # Repo mock to capture inserts without DB
    inserted_atoms = []

    async def fake_insert_atom(session, **kwargs):
        from app.repositories.feedback_atom_repo import FeedbackAtomSchema

        atom = FeedbackAtomSchema(
            id=uuid.uuid4(),
            source_kind=kwargs["source_kind"],
            source_id=kwargs["source_id"],
            mailbox=kwargs["mailbox"],
            atom_text=kwargs["atom_text"],
            role=kwargs["role"],
            applies_when=kwargs.get("applies_when"),
            scope=kwargs["scope"],
            scope_key=kwargs["scope_key"],
            is_active=True,
            hit_count=0,
            precision_num=0,
            precision_den=0,
            person_bound=kwargs.get("person_bound", False),
        )
        inserted_atoms.append(atom)
        return atom

    with patch(
        "app.services.feedback_atom_service.feedback_atom_repo.insert_atom",
        side_effect=fake_insert_atom,
    ):
        result = await atomize_and_persist(
            AsyncMock(),  # session
            mock_anthropic,
            settings,
            mock_openai,
            source_kind="rejection",
            source_id=uuid.uuid4(),
            mailbox=MAILBOX,
            text="The tone was too formal; address Elise by first name",
            thread_id=THREAD_ID,
            sender_address="elise@client.com",
            sender_domain="client.com",
            routing_category="billing",
        )

    # Null atoms are NOT persisted — only Fix and Spec
    roles = [a.role for a in result]
    assert len(result) == 2, f"Expected 2 persisted atoms (Fix + Spec), got {len(result)}: {roles}"
    assert "Fix" in roles
    assert "Spec" in roles
    assert "Null" not in roles


@pytest.mark.asyncio
async def test_atomize_fix_scope_key_never_global() -> None:
    """Atoms persisted by atomize_and_persist never have scope='global'."""
    from app.services.feedback_atom_service import atomize_and_persist

    settings = _make_settings(feedback_atoms_enabled=True)

    haiku_atoms_json = (
        '{"atoms":['
        '{"text":"Use professional sign-off","role":"Fix","applies_when":null,'
        '"suggested_scope":"global"}'  # LLM wrongly suggests global
        "]}"
    )
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=haiku_atoms_json)]

    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create = AsyncMock(return_value=mock_msg)

    mock_embed_response = MagicMock()
    mock_embed_response.data = [MagicMock(embedding=[0.0] * 1536)]
    mock_openai = AsyncMock()
    mock_openai.embeddings.create = AsyncMock(return_value=mock_embed_response)

    inserted_atoms = []

    async def fake_insert_atom(session, **kwargs):
        from app.repositories.feedback_atom_repo import FeedbackAtomSchema

        atom = FeedbackAtomSchema(
            id=uuid.uuid4(),
            source_kind=kwargs["source_kind"],
            source_id=kwargs["source_id"],
            mailbox=kwargs["mailbox"],
            atom_text=kwargs["atom_text"],
            role=kwargs["role"],
            applies_when=kwargs.get("applies_when"),
            scope=kwargs["scope"],
            scope_key=kwargs["scope_key"],
            is_active=True,
            hit_count=0,
            precision_num=0,
            precision_den=0,
            person_bound=kwargs.get("person_bound", False),
        )
        inserted_atoms.append(atom)
        return atom

    with patch(
        "app.services.feedback_atom_service.feedback_atom_repo.insert_atom",
        side_effect=fake_insert_atom,
    ):
        result = await atomize_and_persist(
            AsyncMock(),
            mock_anthropic,
            settings,
            mock_openai,
            source_kind="approval",
            source_id=uuid.uuid4(),
            mailbox=MAILBOX,
            text="Please always use professional sign-off",
            thread_id=THREAD_ID,
            sender_address="x@y.com",
            sender_domain="y.com",
            routing_category="general",
        )

    for atom in result:
        assert atom.scope != "global", "Persisted atom must never have scope='global'"
