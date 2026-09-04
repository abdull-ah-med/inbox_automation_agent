"""Atom extract caps the parsed list so a verbose Haiku JSON cannot explode the pack."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.llm.atom_extract import MAX_EXTRACTED_ATOMS, extract_atoms


def _settings() -> Settings:
    return Settings(
        environment="local",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        classification_model="claude-haiku-4-5",
    )


def _atom(i: int) -> dict:
    return {
        "text": f"Always mention invoice {i:02d} in the first sentence",
        "role": "Fix",
        "applies_when": None,
        "suggested_scope": "mailbox",
    }


@pytest.mark.asyncio
async def test_extract_atoms_caps_at_eight() -> None:
    payload = json.dumps({"atoms": [_atom(i) for i in range(20)]})
    msg = MagicMock()
    msg.content = [MagicMock(text=payload)]
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=msg)

    atoms = await extract_atoms(client, _settings(), feedback_text="Too many rules in this note")

    assert len(atoms) == MAX_EXTRACTED_ATOMS
    assert MAX_EXTRACTED_ATOMS == 8
    assert atoms[0]["text"] == "Always mention invoice 00 in the first sentence"
    assert atoms[-1]["text"] == "Always mention invoice 07 in the first sentence"
