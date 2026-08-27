"""DeepEval CI gate for InboxAssistant RAG — opt-in via pytest -m deepeval.

Skips when API keys or deepeval extras are absent. Thresholds are literals
from baseline.json; a drop of more than 0.05 from baseline fails the build.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.evals.adapters.chat import run_chat_case
from tests.evals.dataset_io import list_chat_v1_cases, list_v1_cases, new_run_dir, write_json
from tests.evals.judge import deepeval_judge_model, require_eval_settings
from tests.live_helpers import env_flag

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"
REGRESSION_TOLERANCE = 0.05

pytestmark = [
    pytest.mark.deepeval,
    pytest.mark.asyncio,
]


def _thresholds() -> dict[str, float]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _skip_unless_gated() -> None:
    if not env_flag("RUN_LLM_EVAL"):
        pytest.skip("Set RUN_LLM_EVAL=1 to run the DeepEval CI gate")


@pytest.mark.deepeval
async def test_faithfulness_gate_meets_baseline_thresholds() -> None:
    _skip_unless_gated()
    require_eval_settings()
    try:
        from deepeval.metrics import (
            AnswerRelevancyMetric,
            ContextualPrecisionMetric,
            ContextualRecallMetric,
            FaithfulnessMetric,
        )
        from deepeval.test_case import LLMTestCase
    except ImportError as exc:
        pytest.skip(f"deepeval not installed ({exc})")

    judge = deepeval_judge_model()
    thresholds = _thresholds()
    cases = []
    seen: set[str] = set()
    for case in [*list_v1_cases(), *list_chat_v1_cases()]:
        case_id = str(case.get("id") or "")
        if case_id in seen or not case.get("hits") or not case.get("question"):
            continue
        seen.add(case_id)
        cases.append(case)
    assert len(cases) >= 3
    scores: dict[str, list[float]] = {
        "FaithfulnessMetric": [],
        "AnswerRelevancyMetric": [],
        "ContextualPrecisionMetric": [],
        "ContextualRecallMetric": [],
    }
    run_dir = new_run_dir("deepeval-gate")
    for case in cases:
        payload = await run_chat_case(case)
        test_case = LLMTestCase(
            input=payload.input_text,
            actual_output=payload.actual_output,
            expected_output=payload.expected_output or payload.actual_output,
            retrieval_context=payload.retrieval_contexts,
        )
        metrics: list[Any] = [
            FaithfulnessMetric(threshold=0.0, model=judge),
            AnswerRelevancyMetric(threshold=0.0, model=judge),
            ContextualPrecisionMetric(threshold=0.0, model=judge),
            ContextualRecallMetric(threshold=0.0, model=judge),
        ]
        for metric in metrics:
            metric.measure(test_case)
            name = type(metric).__name__
            scores[name].append(float(metric.score or 0.0))

    averages = {
        name: (sum(values) / len(values) if values else 0.0) for name, values in scores.items()
    }
    write_json(run_dir / "gate_scores.json", averages)
    for name, floor in thresholds.items():
        avg = averages[name]
        assert avg >= floor - REGRESSION_TOLERANCE, (
            f"{name}={avg:.3f} dropped more than {REGRESSION_TOLERANCE} below baseline {floor}"
        )
        assert avg >= floor or avg >= floor - REGRESSION_TOLERANCE
    assert averages["FaithfulnessMetric"] >= 0.90 - REGRESSION_TOLERANCE
    assert averages["AnswerRelevancyMetric"] >= 0.85 - REGRESSION_TOLERANCE
    assert averages["ContextualPrecisionMetric"] >= 0.80 - REGRESSION_TOLERANCE
    assert averages["ContextualRecallMetric"] >= 0.80 - REGRESSION_TOLERANCE
