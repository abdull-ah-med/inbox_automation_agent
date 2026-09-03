"""Offline unit tests for eval dataset + adapters (no DeepEval/RAGAS, no live LLM)."""

from __future__ import annotations

from tests.evals.adapters.contexts import (
    build_flow_b_context,
    build_grounding_contexts,
    expected_tools_from_case,
    flow_b_chunks,
)
from tests.evals.dataset_io import DATASET_V1, cases_for_suite, list_v1_cases, load_case


def test_v1_dataset_has_expected_cases() -> None:
    cases = list_v1_cases()
    ids = {c["id"] for c in cases}
    assert "samplelab-harmeyer-rebilling" in ids
    assert "scheduling-decoy-no-samplelab" in ids
    assert "flow-b-prior-thread" in ids
    assert "simple-no-context" in ids
    assert "courtesy-close-olivia" in ids
    assert "quoted-reply-new-topic" in ids
    assert "long-thread-summary-query" in ids
    assert "injection-payload" in ids
    assert "cache-paraphrase" in ids
    assert "hallucination-bait" in ids
    assert "operational-fyi-screens-done" in ids
    assert "cc-observer-birthday" in ids
    assert "daily-drivers-action" in ids
    assert "method-lead-cc" in ids


def test_suite_tags_partition() -> None:
    assert {c["id"] for c in cases_for_suite("A")} == {"flow-b-prior-thread"}
    assert "samplelab-harmeyer-rebilling" in {c["id"] for c in cases_for_suite("B")}
    assert "samplelab-harmeyer-rebilling" in {c["id"] for c in cases_for_suite("C")}
    assert "simple-no-context" in {c["id"] for c in cases_for_suite("B")}
    assert "simple-no-context" not in {c["id"] for c in cases_for_suite("C")}


def test_pipeline_suites_exclude_chat_shaped_v1_fixtures() -> None:
    """question+hits gold belongs to InboxAssistant, not generate_draft."""
    chat_shaped = {
        "cache-paraphrase",
        "hallucination-bait",
        "injection-payload",
        "long-thread-summary-query",
        "quoted-reply-new-topic",
    }
    pipeline_ids = {c["id"] for c in cases_for_suite("B")} | {c["id"] for c in cases_for_suite("C")}
    assert chat_shaped.isdisjoint(pipeline_ids)
    assert "courtesy-close-olivia" in {c["id"] for c in cases_for_suite("B")}


def test_flow_b_chunks_ranked_and_nonempty() -> None:
    case = load_case(DATASET_V1 / "flow_b_prior_thread.json")
    cross = build_flow_b_context(case)
    assert cross is not None
    chunks = flow_b_chunks(cross)
    assert len(chunks) == 3
    assert "John Martinez" in chunks[-1]


def test_grounding_contexts_include_skill_and_flow_b() -> None:
    case = load_case(DATASET_V1 / "flow_b_prior_thread.json")
    cross = build_flow_b_context(case)
    contexts = build_grounding_contexts(
        skills=["## Skill: demo\nDo the thing."],
        cross=cross,
        tone_profile="Be brief.",
        tone_references=["Prior approved reply excerpt"],
        negative_constraints=["Do not promise SLAs"],
        loaded_reference_texts=["client_rules: Trolinder spelling"],
    )
    blob = "\n".join(contexts)
    assert "Skill: demo" in blob
    assert "Related prior conversation" in blob or "John Martinez" in blob
    assert "Tone profile" in blob
    assert "Negative constraint" in blob
    assert "Trolinder" in blob


def test_expected_tools_from_samplelab_case() -> None:
    case = load_case(DATASET_V1 / "samplelab_harmeyer_rebilling.json")
    tools = expected_tools_from_case(case)
    assert tools
    assert tools[0]["name"] == "read_skill_reference"
    assert tools[0]["path_prefix"] == "references/"
