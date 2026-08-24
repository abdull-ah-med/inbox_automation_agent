"""DeepEval suites A/B/C — opt-in via RUN_LLM_EVAL=1.

Suite A: Flow B ContextualRelevancy (+ Precision/Recall when gold exists)
Suite B: Faithfulness + AnswerRelevancy on generator grounding contexts
Suite C: ToolCorrectness for read_skill_reference + GEval skill/process criteria

Scores start permissive (threshold=0.0). DeepEval 3.9 ``assert_test`` rejects
``threshold=None`` (MetricData requires a float) even though docs mention
score-only mode.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.evals.adapters.contexts import EvalPayload, run_case
from tests.evals.dataset_io import cases_for_suite, new_run_dir, write_json
from tests.evals.judge import (
    deepeval_judge_model,
    measure_metric_scores,
    require_eval_settings,
)
from tests.live_helpers import env_flag

# Permissive until calibrated — records scores without failing on low quality.
_PERMISSIVE_THRESHOLD = 0.0

pytestmark = [
    pytest.mark.llm_eval,
    pytest.mark.asyncio,
]


def _skip_unless_live() -> None:
    if not env_flag("RUN_LLM_EVAL"):
        pytest.skip("Set RUN_LLM_EVAL=1")


def _deepeval_imports() -> Any:
    try:
        from deepeval.metrics import (
            AnswerRelevancyMetric,
            ContextualPrecisionMetric,
            ContextualRecallMetric,
            ContextualRelevancyMetric,
            FaithfulnessMetric,
            GEval,
            ToolCorrectnessMetric,
        )
        from deepeval.test_case import LLMTestCase, ToolCall
    except ImportError as exc:
        pytest.skip(f'deepeval not installed ({exc}); pip install -e ".[eval]"')

    try:
        from deepeval.test_case import SingleTurnParams as EvalParams
    except ImportError:
        from deepeval.test_case import LLMTestCaseParams as EvalParams  # type: ignore[no-redef]

    return {
        "AnswerRelevancyMetric": AnswerRelevancyMetric,
        "ContextualPrecisionMetric": ContextualPrecisionMetric,
        "ContextualRecallMetric": ContextualRecallMetric,
        "ContextualRelevancyMetric": ContextualRelevancyMetric,
        "FaithfulnessMetric": FaithfulnessMetric,
        "GEval": GEval,
        "ToolCorrectnessMetric": ToolCorrectnessMetric,
        "LLMTestCase": LLMTestCase,
        "ToolCall": ToolCall,
        "EvalParams": EvalParams,
    }


async def _measure_scores(test_case: Any, metrics: list[Any]) -> dict[str, Any]:
    """Capture scores via ``a_measure`` so pytest-asyncio does not deadlock."""
    return await measure_metric_scores(test_case, metrics)



def _to_tool_calls(items: list[dict[str, Any]], ToolCall: Any) -> list[Any]:
    out: list[Any] = []
    for item in items:
        name = item.get("name") or "read_skill_reference"
        params = item.get("input_parameters")
        if params is None and item.get("path"):
            params = {"path": item["path"]}
        kwargs: dict[str, Any] = {"name": name}
        if params:
            kwargs["input_parameters"] = params
        out.append(ToolCall(**kwargs))
    return out


def _expected_tool_calls(payload: EvalPayload, ToolCall: Any) -> list[Any]:
    """Name-level expectations (path args are dynamic UUID/path choices)."""
    if not payload.expected_tools:
        return []
    return [
        ToolCall(name=str(t.get("name") or "read_skill_reference"))
        for t in payload.expected_tools
    ]


@pytest.fixture(scope="module")
def eval_run_dir():
    _skip_unless_live()
    require_eval_settings()
    return new_run_dir("deepeval")


@pytest.mark.parametrize("case", cases_for_suite("A"), ids=lambda c: c["id"])
async def test_deepeval_suite_a_flow_b_retriever(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite A — ContextualRelevancy on Flow B chunks; Precision/Recall when gold exists."""
    de = _deepeval_imports()
    model = deepeval_judge_model()
    # Generation optional for A; still generate so ContextualPrecision has actual_output.
    payload = await run_case(case, generate=True)
    if not payload.flow_b_chunks:
        pytest.skip("Case has no Flow B chunks")

    test_case = de["LLMTestCase"](
        input=payload.input_text,
        actual_output=payload.actual_output or payload.expected_output or "",
        expected_output=payload.expected_output,
        retrieval_context=payload.flow_b_chunks,
    )
    metrics = [
        de["ContextualRelevancyMetric"](
            threshold=_PERMISSIVE_THRESHOLD, model=model, include_reason=True
        ),
    ]
    if payload.expected_output:
        metrics.extend(
            [
                de["ContextualPrecisionMetric"](
                    threshold=_PERMISSIVE_THRESHOLD, model=model, include_reason=True
                ),
                de["ContextualRecallMetric"](
                    threshold=_PERMISSIVE_THRESHOLD, model=model, include_reason=True
                ),
            ]
        )

    scores = await _measure_scores(test_case, metrics)
    write_json(
        eval_run_dir / f"suite_a_{payload.case_id}.json",
        {"case_id": payload.case_id, "framework": "deepeval", "suite": "A", "scores": scores},
    )


@pytest.mark.parametrize("case", cases_for_suite("B"), ids=lambda c: c["id"])
async def test_deepeval_suite_b_generator(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite B — Faithfulness + AnswerRelevancy against generator grounding contexts."""
    de = _deepeval_imports()
    model = deepeval_judge_model()
    payload = await run_case(case, generate=True)
    assert payload.actual_output.strip(), "generate_draft returned empty reply_body"

    contexts = payload.grounding_contexts or [payload.input_text]
    test_case = de["LLMTestCase"](
        input=payload.input_text,
        actual_output=payload.actual_output,
        retrieval_context=contexts,
    )
    metrics = [
        de["FaithfulnessMetric"](
            threshold=_PERMISSIVE_THRESHOLD, model=model, include_reason=True
        ),
        de["AnswerRelevancyMetric"](
            threshold=_PERMISSIVE_THRESHOLD, model=model, include_reason=True
        ),
    ]
    scores = await _measure_scores(test_case, metrics)
    write_json(
        eval_run_dir / f"suite_b_{payload.case_id}.json",
        {
            "case_id": payload.case_id,
            "framework": "deepeval",
            "suite": "B",
            "scores": scores,
            "context_count": len(contexts),
            "urgency": payload.urgency,
        },
    )


@pytest.mark.parametrize("case", cases_for_suite("C"), ids=lambda c: c["id"])
async def test_deepeval_suite_c_tools_and_geval(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite C — ToolCorrectness for skill references + GEval domain criteria."""
    de = _deepeval_imports()
    model = deepeval_judge_model()
    payload = await run_case(case, generate=True)
    assert payload.actual_output.strip()

    tools_called = _to_tool_calls(payload.tools_called, de["ToolCall"])
    expected_tools = _expected_tool_calls(payload, de["ToolCall"])

    tool_case = de["LLMTestCase"](
        input=payload.input_text,
        actual_output=payload.actual_output,
        tools_called=tools_called,
        expected_tools=expected_tools,
    )
    tool_metric = de["ToolCorrectnessMetric"](
        threshold=_PERMISSIVE_THRESHOLD, include_reason=True
    )
    tool_scores = await _measure_scores(tool_case, [tool_metric])

    criteria = payload.metadata.get("geval_criteria") or (
        "Draft quality for shared-inbox reply suggestions."
    )
    eval_params = [
        de["EvalParams"].INPUT,
        de["EvalParams"].ACTUAL_OUTPUT,
    ]
    # Prefer grounding context when the enum exposes it.
    retrieval_param = getattr(de["EvalParams"], "RETRIEVAL_CONTEXT", None)
    if retrieval_param is not None:
        eval_params.append(retrieval_param)

    geval = de["GEval"](
        name="SkillOrProcessAdherence",
        criteria=str(criteria),
        evaluation_params=eval_params,
        threshold=_PERMISSIVE_THRESHOLD,
        model=model,
    )
    geval_case = de["LLMTestCase"](
        input=payload.input_text,
        actual_output=payload.actual_output,
        retrieval_context=payload.grounding_contexts or None,
    )
    geval_scores = await _measure_scores(geval_case, [geval])

    # Soft domain checks — informational, do not fail the suite in v1.
    forbidden = [f for f in payload.metadata.get("forbidden_facts", []) if f]
    forbidden_hits = [f for f in forbidden if f.lower() in payload.actual_output.lower()]

    write_json(
        eval_run_dir / f"suite_c_{payload.case_id}.json",
        {
            "case_id": payload.case_id,
            "framework": "deepeval",
            "suite": "C",
            "scores": {**tool_scores, **geval_scores},
            "tools_called": payload.tools_called,
            "expected_tools": payload.expected_tools,
            "loaded_reference_paths": payload.loaded_reference_paths,
            "forbidden_hits": forbidden_hits,
        },
    )
