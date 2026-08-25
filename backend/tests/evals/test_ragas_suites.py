"""RAGAS collections-API suites — opt-in via RUN_LLM_EVAL=1.

Suite A: ContextUtilization (no gold) or ContextPrecision (with reference)
Suite B: Faithfulness + AnswerRelevancy (+ NoiseSensitivity when gold exists)
Suite C: name/path tool checks for read_skill_reference on skill cases

Uses ragas.metrics.collections (not the legacy API).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.evals.adapters.contexts import run_case
from tests.evals.dataset_io import cases_for_suite, new_run_dir, write_json
from tests.evals.judge import (
    ragas_embedding_model_name,
    ragas_judge_model_name,
    require_eval_settings,
)

# Cap grounding context size so Faithfulness NLI verdicts fit judge max_tokens.
_MAX_CONTEXT_CHARS = 12_000

pytestmark = [
    pytest.mark.llm_eval,
    pytest.mark.asyncio,
]


def _truncate_contexts(contexts: list[str], *, max_chars: int = _MAX_CONTEXT_CHARS) -> list[str]:
    """Keep contexts within a budget so judge completions are not truncated."""
    out: list[str] = []
    remaining = max_chars
    for ctx in contexts:
        if remaining <= 0:
            break
        text = ctx if len(ctx) <= remaining else ctx[:remaining] + "\n…[truncated for eval]"
        out.append(text)
        remaining -= len(text)
    return out or contexts[:1]


def _ragas_stack() -> dict[str, Any]:
    try:
        from openai import AsyncOpenAI
        from ragas.embeddings.base import embedding_factory
        from ragas.llms import llm_factory
        from ragas.metrics.collections import (
            AnswerRelevancy,
            ContextPrecision,
            ContextUtilization,
            Faithfulness,
            NoiseSensitivity,
        )
    except ImportError as exc:
        pytest.skip(f'ragas not installed ({exc}); pip install -e ".[eval]"')

    settings = require_eval_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    # Higher max_tokens avoids IncompleteOutputException on large skill contexts.
    llm = llm_factory(ragas_judge_model_name(), client=client, max_tokens=4096)
    embeddings = embedding_factory(
        "openai",
        model=ragas_embedding_model_name(),
        client=client,
    )
    return {
        "llm": llm,
        "embeddings": embeddings,
        "Faithfulness": Faithfulness,
        "AnswerRelevancy": AnswerRelevancy,
        "ContextUtilization": ContextUtilization,
        "ContextPrecision": ContextPrecision,
        "NoiseSensitivity": NoiseSensitivity,
    }


def _name_level_tool_score(
    *,
    tools_called: list[dict[str, Any]],
    expected_tools: list[dict[str, Any]],
) -> tuple[float, bool]:
    """Score tool use without RAGAS ToolCallAccuracy arg/length mismatches.

    Expected empty + observed empty → 1.0
    Expected empty + observed tools → 0.0 (decoy precision)
    Expected name(s) present in observed → 1.0 when path_prefix also matches
    """
    path_ok = True
    for expected in expected_tools:
        prefix = expected.get("path_prefix")
        if not prefix:
            continue
        path_ok = any(
            str(t.get("path") or "").startswith(str(prefix)) for t in tools_called
        )
        if not path_ok:
            break

    if not expected_tools:
        return (1.0 if not tools_called else 0.0), path_ok

    expected_names = {
        str(t.get("name") or "read_skill_reference") for t in expected_tools
    }
    called_names = {str(t.get("name")) for t in tools_called}
    if not (expected_names & called_names):
        return 0.0, path_ok
    return (1.0 if path_ok else 0.5), path_ok


@pytest.fixture(scope="module")
def eval_run_dir():
    require_eval_settings()
    return new_run_dir("ragas")


@pytest.mark.parametrize("case", cases_for_suite("A"), ids=lambda c: c["id"])
async def test_ragas_suite_a_retriever(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite A — context utilization/precision on Flow B chunks."""
    stack = _ragas_stack()
    payload = await run_case(case, generate=True)
    if not payload.flow_b_chunks:
        pytest.skip("Case has no Flow B chunks")

    scores: dict[str, Any] = {}
    if payload.expected_output:
        scorer = stack["ContextPrecision"](llm=stack["llm"])
        result = await scorer.ascore(
            user_input=payload.input_text,
            reference=payload.expected_output,
            retrieved_contexts=payload.flow_b_chunks,
        )
        scores["ContextPrecision"] = result.value
    else:
        scorer = stack["ContextUtilization"](llm=stack["llm"])
        result = await scorer.ascore(
            user_input=payload.input_text,
            response=payload.actual_output,
            retrieved_contexts=payload.flow_b_chunks,
        )
        scores["ContextUtilization"] = result.value

    write_json(
        eval_run_dir / f"suite_a_{payload.case_id}.json",
        {"case_id": payload.case_id, "framework": "ragas", "suite": "A", "scores": scores},
    )


@pytest.mark.parametrize("case", cases_for_suite("B"), ids=lambda c: c["id"])
async def test_ragas_suite_b_generator(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite B — Faithfulness + AnswerRelevancy; NoiseSensitivity when gold exists."""
    stack = _ragas_stack()
    payload = await run_case(case, generate=True)
    assert payload.actual_output.strip()

    contexts = _truncate_contexts(payload.grounding_contexts or [payload.input_text])
    scores: dict[str, Any] = {}

    faith = stack["Faithfulness"](llm=stack["llm"])
    faith_result = await faith.ascore(
        user_input=payload.input_text,
        response=payload.actual_output,
        retrieved_contexts=contexts,
    )
    scores["Faithfulness"] = faith_result.value

    ans = stack["AnswerRelevancy"](llm=stack["llm"], embeddings=stack["embeddings"])
    ans_result = await ans.ascore(
        user_input=payload.input_text,
        response=payload.actual_output,
    )
    scores["AnswerRelevancy"] = ans_result.value

    if payload.expected_output:
        noise = stack["NoiseSensitivity"](llm=stack["llm"])
        noise_result = await noise.ascore(
            user_input=payload.input_text,
            response=payload.actual_output,
            reference=payload.expected_output,
            retrieved_contexts=contexts,
        )
        scores["NoiseSensitivity"] = noise_result.value

    write_json(
        eval_run_dir / f"suite_b_{payload.case_id}.json",
        {
            "case_id": payload.case_id,
            "framework": "ragas",
            "suite": "B",
            "scores": scores,
            "context_count": len(contexts),
            "contexts_truncated": contexts != (payload.grounding_contexts or [payload.input_text]),
            "note": (
                "RAGAS Faithfulness requires claims to be inferable from context; "
                "email greetings/hedging often lower this vs DeepEval."
            ),
        },
    )


@pytest.mark.parametrize("case", cases_for_suite("C"), ids=lambda c: c["id"])
async def test_ragas_suite_c_tool_accuracy(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite C — name/path_prefix tool checks for skill reference loading."""
    # Still need stack import gate for eval deps presence.
    _ragas_stack()
    payload = await run_case(case, generate=True)

    score, path_ok = _name_level_tool_score(
        tools_called=payload.tools_called,
        expected_tools=payload.expected_tools,
    )

    write_json(
        eval_run_dir / f"suite_c_{payload.case_id}.json",
        {
            "case_id": payload.case_id,
            "framework": "ragas",
            "suite": "C",
            "scores": {
                "ToolNamePathScore": score,
                "path_prefix_ok": path_ok,
            },
            "tools_called": payload.tools_called,
            "expected_tools": payload.expected_tools,
            "loaded_reference_paths": payload.loaded_reference_paths,
            "note": (
                "Uses name/path_prefix scoring instead of RAGAS ToolCallAccuracy, "
                "which penalizes arg mismatches (dynamic skill_id/path) and length "
                "mismatches when Sonnet loads multiple references."
            ),
        },
    )

    if payload.expected_tools:
        expected_names = {
            str(t.get("name") or "read_skill_reference") for t in payload.expected_tools
        }
        called_names = {str(t.get("name")) for t in payload.tools_called}
        assert expected_names & called_names, (
            f"Expected tool(s) {expected_names} not found in {called_names}"
        )
        assert path_ok, "Expected reference path_prefix not found in tools_called"
    else:
        assert not payload.tools_called, (
            f"Decoy case expected no tools, got {payload.tools_called}"
        )
