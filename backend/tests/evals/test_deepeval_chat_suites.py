"""DeepEval InboxAssistant suites A/B/C — opt-in via RUN_LLM_EVAL=1.

Suite A: ContextualRelevancy (+ Precision/Recall when gold exists)
Suite B: Faithfulness + AnswerRelevancy on retrieved hit blocks
Suite C: GEval for write-refusal and no-match policy answers

Scores start permissive (threshold=0.0).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.llm.chat_prompts import NO_MATCH_ANSWER, WRITE_REFUSAL_ANSWER
from tests.evals.adapters.chat import run_chat_case
from tests.evals.dataset_io import chat_cases_for_suite, new_run_dir, write_json
from tests.evals.judge import (
    deepeval_judge_model,
    measure_metric_scores,
    require_eval_settings,
)

_PERMISSIVE_THRESHOLD = 0.0

pytestmark = [
    pytest.mark.llm_eval,
    pytest.mark.asyncio,
]


def _deepeval_imports() -> Any:
    try:
        from deepeval.metrics import (
            AnswerRelevancyMetric,
            ContextualPrecisionMetric,
            ContextualRecallMetric,
            ContextualRelevancyMetric,
            FaithfulnessMetric,
            GEval,
        )
        from deepeval.test_case import LLMTestCase
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
        "LLMTestCase": LLMTestCase,
        "EvalParams": EvalParams,
    }


async def _measure_scores(test_case: Any, metrics: list[Any]) -> dict[str, Any]:
    return await measure_metric_scores(test_case, metrics)


@pytest.fixture(scope="module")
def eval_run_dir():
    require_eval_settings()
    return new_run_dir("deepeval-chat")


@pytest.mark.parametrize("case", chat_cases_for_suite("A"), ids=lambda c: c["id"])
async def test_deepeval_chat_suite_a_retriever(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite A — ContextualRelevancy on InboxAssistant hit blocks; Precision/Recall when gold exists."""
    de = _deepeval_imports()
    model = deepeval_judge_model()
    payload = await run_chat_case(case, generate=True)
    if not payload.retrieval_contexts:
        pytest.skip("Case has no retrieved hit contexts")

    test_case = de["LLMTestCase"](
        input=payload.input_text,
        actual_output=payload.actual_output or payload.expected_output or "",
        expected_output=payload.expected_output,
        retrieval_context=payload.retrieval_contexts,
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
        {
            "case_id": payload.case_id,
            "framework": "deepeval",
            "product": "inboxassistant",
            "suite": "A",
            "scores": scores,
        },
    )


@pytest.mark.parametrize("case", chat_cases_for_suite("B"), ids=lambda c: c["id"])
async def test_deepeval_chat_suite_b_generator(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite B — Faithfulness + AnswerRelevancy against retrieved hit blocks."""
    de = _deepeval_imports()
    model = deepeval_judge_model()
    payload = await run_chat_case(case, generate=True)
    assert payload.actual_output.strip(), "InboxAssistant returned an empty answer"

    contexts = payload.retrieval_contexts or [payload.input_text]
    test_case = de["LLMTestCase"](
        input=payload.input_text,
        actual_output=payload.actual_output,
        retrieval_context=contexts,
    )
    metrics = [
        de["FaithfulnessMetric"](threshold=_PERMISSIVE_THRESHOLD, model=model, include_reason=True),
        de["AnswerRelevancyMetric"](
            threshold=_PERMISSIVE_THRESHOLD, model=model, include_reason=True
        ),
    ]
    scores = await _measure_scores(test_case, metrics)
    forbidden = [f for f in payload.metadata.get("forbidden_facts", []) if f]
    forbidden_hits = [f for f in forbidden if f.lower() in payload.actual_output.lower()]
    write_json(
        eval_run_dir / f"suite_b_{payload.case_id}.json",
        {
            "case_id": payload.case_id,
            "framework": "deepeval",
            "product": "inboxassistant",
            "suite": "B",
            "scores": scores,
            "context_count": len(contexts),
            "forbidden_hits": forbidden_hits,
            "answer": payload.actual_output,
        },
    )


@pytest.mark.parametrize("case", chat_cases_for_suite("C"), ids=lambda c: c["id"])
async def test_deepeval_chat_suite_c_policy(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite C — write-refusal / no-match canned answers + GEval policy criteria."""
    de = _deepeval_imports()
    model = deepeval_judge_model()
    payload = await run_chat_case(case, generate=True)
    assert payload.actual_output.strip()

    if payload.case_id == "chat-write-refusal":
        assert payload.refused_write is True
        assert WRITE_REFUSAL_ANSWER in payload.actual_output
    elif payload.case_id == "chat-no-match":
        assert payload.refused_write is False
        assert payload.actual_output == NO_MATCH_ANSWER
        assert payload.retrieval_count == 0

    eval_params = [
        de["EvalParams"].INPUT,
        de["EvalParams"].ACTUAL_OUTPUT,
    ]
    retrieval_param = getattr(de["EvalParams"], "RETRIEVAL_CONTEXT", None)
    if retrieval_param is not None and payload.retrieval_contexts:
        eval_params.append(retrieval_param)

    criteria = payload.metadata.get("geval_criteria") or (
        "InboxAssistant must stay read-only and must not invent threads."
    )
    geval = de["GEval"](
        name="InboxAssistantPolicy",
        criteria=str(criteria),
        evaluation_params=eval_params,
        threshold=_PERMISSIVE_THRESHOLD,
        model=model,
    )
    geval_case = de["LLMTestCase"](
        input=payload.input_text,
        actual_output=payload.actual_output,
        retrieval_context=payload.retrieval_contexts or None,
    )
    scores = await _measure_scores(geval_case, [geval])
    write_json(
        eval_run_dir / f"suite_c_{payload.case_id}.json",
        {
            "case_id": payload.case_id,
            "framework": "deepeval",
            "product": "inboxassistant",
            "suite": "C",
            "scores": scores,
            "refused_write": payload.refused_write,
            "retrieval_count": payload.retrieval_count,
            "answer": payload.actual_output,
        },
    )
