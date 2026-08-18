"""RAGAS InboxAssistant suites — opt-in via RUN_LLM_EVAL=1.

Suite A: ContextUtilization (no gold) or ContextPrecision (with reference)
Suite B: Faithfulness + AnswerRelevancy (+ NoiseSensitivity when gold exists)
Suite C: canned write-refusal / no-match oracles + AnswerRelevancy

Uses ragas.metrics.collections (not the legacy API).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.llm.chat_prompts import NO_MATCH_ANSWER, WRITE_REFUSAL_ANSWER
from tests.evals.adapters.chat import run_chat_case
from tests.evals.dataset_io import chat_cases_for_suite, new_run_dir, write_json
from tests.evals.judge import (
    ragas_embedding_model_name,
    ragas_judge_model_name,
    require_eval_settings,
)
from tests.live_helpers import env_flag

_MAX_CONTEXT_CHARS = 12_000

pytestmark = [
    pytest.mark.llm_eval,
    pytest.mark.asyncio,
]


def _skip_unless_live() -> None:
    if not env_flag("RUN_LLM_EVAL"):
        pytest.skip("Set RUN_LLM_EVAL=1")


def _truncate_contexts(contexts: list[str], *, max_chars: int = _MAX_CONTEXT_CHARS) -> list[str]:
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


@pytest.fixture(scope="module")
def eval_run_dir():
    _skip_unless_live()
    require_eval_settings()
    return new_run_dir("ragas-chat")


@pytest.mark.parametrize("case", chat_cases_for_suite("A"), ids=lambda c: c["id"])
async def test_ragas_chat_suite_a_retriever(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite A — context utilization/precision on InboxAssistant hit blocks."""
    stack = _ragas_stack()
    payload = await run_chat_case(case, generate=True)
    if not payload.retrieval_contexts:
        pytest.skip("Case has no retrieved hit contexts")

    scores: dict[str, Any] = {}
    if payload.expected_output:
        scorer = stack["ContextPrecision"](llm=stack["llm"])
        result = await scorer.ascore(
            user_input=payload.input_text,
            reference=payload.expected_output,
            retrieved_contexts=payload.retrieval_contexts,
        )
        scores["ContextPrecision"] = result.value
    else:
        scorer = stack["ContextUtilization"](llm=stack["llm"])
        result = await scorer.ascore(
            user_input=payload.input_text,
            response=payload.actual_output,
            retrieved_contexts=payload.retrieval_contexts,
        )
        scores["ContextUtilization"] = result.value

    write_json(
        eval_run_dir / f"suite_a_{payload.case_id}.json",
        {
            "case_id": payload.case_id,
            "framework": "ragas",
            "product": "inboxassistant",
            "suite": "A",
            "scores": scores,
        },
    )


@pytest.mark.parametrize("case", chat_cases_for_suite("B"), ids=lambda c: c["id"])
async def test_ragas_chat_suite_b_generator(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite B — Faithfulness + AnswerRelevancy; NoiseSensitivity when gold exists."""
    stack = _ragas_stack()
    payload = await run_chat_case(case, generate=True)
    assert payload.actual_output.strip()

    contexts = _truncate_contexts(payload.retrieval_contexts or [payload.input_text])
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

    forbidden = [f for f in payload.metadata.get("forbidden_facts", []) if f]
    forbidden_hits = [f for f in forbidden if f.lower() in payload.actual_output.lower()]
    write_json(
        eval_run_dir / f"suite_b_{payload.case_id}.json",
        {
            "case_id": payload.case_id,
            "framework": "ragas",
            "product": "inboxassistant",
            "suite": "B",
            "scores": scores,
            "context_count": len(contexts),
            "forbidden_hits": forbidden_hits,
            "answer": payload.actual_output,
            "note": (
                "RAGAS Faithfulness requires claims to be inferable from context; "
                "hedging and UI guidance often lower this vs DeepEval."
            ),
        },
    )


@pytest.mark.parametrize("case", chat_cases_for_suite("C"), ids=lambda c: c["id"])
async def test_ragas_chat_suite_c_policy(
    case: dict[str, Any],
    eval_run_dir: Any,
    require_eval_deps: None,
) -> None:
    """Suite C — canned policy answers plus AnswerRelevancy."""
    stack = _ragas_stack()
    payload = await run_chat_case(case, generate=True)

    if payload.case_id == "chat-write-refusal":
        assert payload.refused_write is True
        assert WRITE_REFUSAL_ANSWER in payload.actual_output
    elif payload.case_id == "chat-no-match":
        assert payload.actual_output == NO_MATCH_ANSWER
        assert payload.retrieval_count == 0

    ans = stack["AnswerRelevancy"](llm=stack["llm"], embeddings=stack["embeddings"])
    ans_result = await ans.ascore(
        user_input=payload.input_text,
        response=payload.actual_output,
    )
    write_json(
        eval_run_dir / f"suite_c_{payload.case_id}.json",
        {
            "case_id": payload.case_id,
            "framework": "ragas",
            "product": "inboxassistant",
            "suite": "C",
            "scores": {"AnswerRelevancy": ans_result.value},
            "refused_write": payload.refused_write,
            "retrieval_count": payload.retrieval_count,
            "answer": payload.actual_output,
        },
    )
