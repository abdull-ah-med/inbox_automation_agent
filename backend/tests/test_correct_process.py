"""Reject process_note and regenerate instruction — schema oracles.

Worked lengths are the field caps: process 2000, instruction 2000.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.feedback import DraftRejectSchema, RegenerateDraftSchema


def test_blank_process_note_becomes_none() -> None:
    body = DraftRejectSchema(
        feedback_note="Tone is too curt",
        reason_code="tone",
        process_note="   ",
    )
    assert body.process_note is None


def test_process_note_is_stripped() -> None:
    body = DraftRejectSchema(
        feedback_note="DraftAssistant drafted a letter. This is an SampleLab invoice.",
        reason_code="incomplete",
        process_note=(
            "  In this case I would process the SampleLab invoice and tell Beau "
            "the rebill is with Harmeyer.  "
        ),
    )
    assert body.process_note == (
        "In this case I would process the SampleLab invoice and tell Beau "
        "the rebill is with Harmeyer."
    )


def test_process_note_over_2000_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DraftRejectSchema(
            feedback_note="Wrong process",
            reason_code="incomplete",
            process_note="a" * 2001,
        )


def test_regenerate_instruction_allows_2000_chars() -> None:
    body = RegenerateDraftSchema(instruction="a" * 2000)
    assert len(body.instruction) == 2000


def test_regenerate_instruction_rejects_2001_chars() -> None:
    with pytest.raises(ValidationError):
        RegenerateDraftSchema(instruction="a" * 2001)
