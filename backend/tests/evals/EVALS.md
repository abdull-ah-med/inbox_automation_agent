# LLM response evals (DeepEval + RAGAS)

Opt-in quality evaluation for draft generation, Flow B retrieval, and skill tool use.
These are **not** smoke tests — they score LLM outputs with LLM-as-judge metrics.

## Install

```bash
cd backend
.venv/bin/pip install -e ".[eval]"
# or: uv pip install -e ".[eval]" --python .venv/bin/python
```

The `[eval]` extra pins `langchain-community>=0.3,<0.4` because RAGAS 0.4.x still
imports `langchain_community.chat_models.vertexai` at import time (removed in 0.4+).

Do **not** install `deepeval[inspect]` unless you need the inspect TUI (DeepEval docs: it bloats installs).

Requires `ANTHROPIC_API_KEY` (generation under test) and `OPENAI_API_KEY` (judge + RAGAS embeddings).

Optional:

| Env | Default | Purpose |
|-----|---------|---------|
| `EVAL_JUDGE_MODEL` | `gpt-4o-mini` | OpenAI judge model for DeepEval + RAGAS |
| `EVAL_EMBEDDING_MODEL` | `text-embedding-3-small` | RAGAS AnswerRelevancy embeddings |

Confident AI / `deepeval login` is **not** required.

## Run

```bash
cd backend
set -a && source .env && set +a

# Full opt-in suite (calls Anthropic Sonnet + OpenAI judge — costs money / time)
RUN_LLM_EVAL=1 .venv/bin/pytest tests/evals -vv -s -m llm_eval

# Or one framework
RUN_LLM_EVAL=1 .venv/bin/pytest tests/evals/test_deepeval_suites.py -vv -s
RUN_LLM_EVAL=1 .venv/bin/pytest tests/evals/test_ragas_suites.py -vv -s
```

Artifacts land in `tests/artifacts/evals/<run_id>/` (gitignored).

Note: installing DeepEval registers a pytest plugin. Offline/default runs still
skip `llm_eval` without `RUN_LLM_EVAL=1`. If the plugin is noisy locally, you can
run with `-p no:deepeval`.

### Export approved drafts from local DB (read-only)

```bash
.venv/bin/python -m tests.evals.exporters.db_approved_drafts --limit 50
```

Writes `tests/artifacts/evals/datasets/approved_drafts_*.json`. **Do not commit** — may contain residual PII after scrubbing.

## Suites (component-split)

Our pipeline is not classic single-retriever RAG. Contexts are scoped per suite:

| Suite | What | DeepEval | RAGAS |
|-------|------|----------|-------|
| **A** Flow B retriever | Ranked related-thread chunks only | ContextualRelevancy (+ Precision/Recall if gold) | ContextUtilization or ContextPrecision |
| **B** Generator grounding | Skills + loaded refs + Flow B pack + tone + negatives | Faithfulness, AnswerRelevancy | Faithfulness, AnswerRelevancy, NoiseSensitivity (if gold) |
| **C** Skill tools | `read_skill_reference` tool loop | ToolCorrectness, GEval | ToolCallAccuracy (+ path_prefix check) |

Field contracts:

- `input` / `user_input` = scrubbed subject + body only (never the system prompt)
- `actual_output` / `response` = `reply_body` only for RAG metrics
- `retrieval_context` / `retrieved_contexts` = suite-scoped chunks (see adapters)

## Faithfulness divergence (important)

| | DeepEval | RAGAS |
|---|---|---|
| Claim passes if… | Does **not contradict** context | Can be **inferred from** context |
| Greetings / hedging | Often faithful | Often unfaithful |

Interpret scores **side-by-side**. Do not average them into one number.

## Dataset

- **Curated (committed):** `tests/evals/dataset/v1/*.json` — synthetic, no live mailbox PII
- **DB export (gitignored):** approved drafts via the exporter; today local gold is thin (~3 approved)

Grow gold by approving more drafts in the product, not by fabricating references.

## Latest live run (2026-08-06, ~10.5 min)

**Pytest:** 13 passed, 1 failed (RAGAS Suite B samplelab Faithfulness judge hit
`IncompleteOutputException` / max_tokens on large skill context).

### Behavioral signals (from draft logs)

| Case | Tools | Urgency |
|------|-------|---------|
| samplelab Harmeyer | 2–3× `read_skill_reference` (departments, client_rules, output_format) | NORMAL |
| scheduling decoy | 0 tools (correct) | NORMAL |
| flow-b prior thread | 0 tools | HIGH |
| simple no-context | 0 tools | LOW |

### RAGAS scores that were written

| Case | Faithfulness | Answer Relevancy | Other |
|------|-------------:|-----------------:|-------|
| flow-b-prior-thread | 0.20 | 0.72 | ContextPrecision ≈ 1.0; NoiseSensitivity 0.0 |
| scheduling-decoy | 0.00 | 0.61 | Tool score 1.0 (no tools) |
| simple-no-context | 0.00 | 0.43 | — |
| samplelab Suite B | *(failed — judge truncated)* | — | Suite C path_prefix_ok=true; ToolCallAccuracy 0.0 due to arg/length mismatch |

Low RAGAS Faithfulness on short cases is expected (claims must be *inferable* from
retrieved context; greetings/hedging count against). DeepEval Suite A/B/C all
**passed**, but artifacts had `score: null` because `assert_test` does not leave
scores on metric objects — fixed to use `measure()` for capture.

### Fixes after that run

- DeepEval: capture scores via `measure()` (not `assert_test` side effects)
- RAGAS: truncate large grounding contexts + `max_tokens=4096` for judge
- RAGAS Suite C: name/path_prefix scoring instead of ToolCallAccuracy arg matching

## Calibration / CI

v1 DeepEval metrics use permissive `threshold=0.0`. Default CI does **not** install
`[eval]` and does **not** set `RUN_LLM_EVAL`. Do not gate merges until you have a
larger labeled set (~50+ cases) and tuned thresholds.


## PII rules

- Curated fixtures must stay synthetic
- DB exports are scrubbed with `scrub_text` but may still contain residual identifiers — review before sharing
- Never commit `tests/artifacts/`
