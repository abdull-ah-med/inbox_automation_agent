"""Tests for applies_when_gate.

Oracle source: plan §5 spec:
  - Flag off → all True (no API call).
  - Flag on + mock Haiku that drops one → correct mask returned.
  - None conditions (unconditional atoms) always True regardless of gate.
  - Empty candidates → empty result.

Haiku is mocked at the AsyncAnthropic boundary only.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.services.applies_when_gate import gate_atoms_and_notes


def _make_settings(gate_enabled: bool = False) -> Settings:
    return Settings(
        graph_client_id="x",
        graph_client_secret="x",
        graph_tenant_id="x",
        environment="local",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        applies_when_gate_enabled=gate_enabled,
    )


EMAIL = "Hi, please confirm the shipment for invoice #INV-2026-001."


@pytest.mark.asyncio
async def test_gate_off_returns_all_true() -> None:
    """When flag is off, all candidates pass through without any API call."""
    settings = _make_settings(gate_enabled=False)
    mock_client = AsyncMock()

    candidates = [
        "when the invoice amount exceeds $1000",
        "when sender is from acme.com",
        None,  # unconditional
    ]
    result = await gate_atoms_and_notes(mock_client, settings, EMAIL, candidates)

    assert result == [True, True, True]
    mock_client.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_gate_off_empty_list() -> None:
    """Empty candidates list returns empty result (gate off)."""
    settings = _make_settings(gate_enabled=False)
    mock_client = AsyncMock()

    result = await gate_atoms_and_notes(mock_client, settings, EMAIL, [])
    assert result == []


@pytest.mark.asyncio
async def test_gate_on_drops_one_candidate() -> None:
    """Gate on: Haiku says second condition does NOT apply; mask is [True, False, True]."""
    settings = _make_settings(gate_enabled=True)

    # Mock Haiku: 2 conditional + 1 unconditional (None)
    # Haiku only sees the 2 conditional → returns [True, False]
    haiku_response_json = json.dumps({"applies": [True, False]})
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=haiku_response_json)]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_msg)

    candidates = [
        "when the invoice amount exceeds $1000",  # index 0, conditional
        "when sender is from unrelated.com",  # index 1, conditional — Haiku says False
        None,  # index 2, unconditional → always True
    ]

    result = await gate_atoms_and_notes(mock_client, settings, EMAIL, candidates)

    # index 0: Haiku True → True
    # index 1: Haiku False → False
    # index 2: unconditional → True
    assert result == [True, False, True]
    mock_client.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_gate_on_all_unconditional_skips_api() -> None:
    """Gate on but all candidates are None (unconditional) → all True, no API call."""
    settings = _make_settings(gate_enabled=True)
    mock_client = AsyncMock()

    result = await gate_atoms_and_notes(mock_client, settings, EMAIL, [None, None])
    assert result == [True, True]
    mock_client.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_gate_on_api_failure_fails_closed() -> None:
    """API failure with gate on drops conditional candidates (fail-closed)."""
    settings = _make_settings(gate_enabled=True)
    mock_client = AsyncMock()
    mock_client.messages.create.side_effect = RuntimeError("network error")

    candidates = ["when invoice > $500", "when sender from acme.com", None]
    result = await gate_atoms_and_notes(mock_client, settings, EMAIL, candidates)

    assert result == [False, False, True]
