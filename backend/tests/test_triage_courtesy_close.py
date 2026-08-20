"""Courtesy/close goldens: frozen triage JSON → discard. Do not parse the prompt."""

from __future__ import annotations

from app.models.schemas.classification import TriageResultSchema
from app.services.triage_service import decide_triage_outcome
from tests.evals.dataset_io import DATASET_V1, load_case


def _outcome_for(path: str) -> tuple[bool, str]:
    case = load_case(DATASET_V1 / path)
    triage = TriageResultSchema.model_validate(
        {
            "is_spam": case["triage"]["is_spam"],
            "spam_reason": None,
            "has_action_items": case["triage"]["has_action_items"],
            "action_items_summary": case["triage"]["action_items_summary"],
            "needs_context": case["triage"]["needs_context"],
            "context_reason": None,
            "routing_category": case["triage"]["routing_category"],
        }
    )
    outcome, _status = decide_triage_outcome(triage)
    return triage.has_action_items, outcome


def test_olivia_courtesy_close_is_no_action_discarded() -> None:
    has_action, outcome = _outcome_for("courtesy_close_olivia.json")
    assert has_action is False
    assert outcome == "no_action_discarded"


def test_billing_ask_still_needs_action() -> None:
    has_action, outcome = _outcome_for("samplelab_harmeyer_rebilling.json")
    assert has_action is True
    assert outcome == "action_needed"
