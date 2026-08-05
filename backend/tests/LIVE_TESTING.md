# Live end-to-end tests

All tests here hit **real** Microsoft Graph, Anthropic, OpenAI, Postgres, Redis,
and (when configured) Slack. Nothing is mocked.

Artifacts from the master suite land in `tests/artifacts/live/<run_id>/`
(gitignored).

## Prerequisites

```bash
cd backend
set -a && source .env && set +a
.venv/bin/alembic upgrade head
```

Requires Graph + Anthropic + OpenAI keys, `TARGET_MAILBOXES`, migrated DB, Redis.
For Slack cards: set `SLACK_ENABLED=true` plus bot token / signing secret / channel.

## Run

```bash
# Master live journey (Graph → triage → draft → Slack → web API)
SLACK_ENABLED=true RUN_LIVE_COMPLETE=1 LIVE_MAX_MESSAGES=2 \
  .venv/bin/pytest tests/test_live_complete.py -vv -s

# Individual suites
RUN_LIVE_GRAPH=1 .venv/bin/pytest tests/test_live_graph_smoke.py -vv -s
RUN_LIVE_CLAUDE=1 .venv/bin/pytest tests/test_live_claude_triage.py -vv -s
RUN_LIVE_E2E=1 .venv/bin/pytest tests/test_live_e2e_pipeline.py -vv -s
RUN_LIVE_EMBEDDING_E2E=1 .venv/bin/pytest tests/test_live_embedding_e2e.py -vv -s
```

## Coverage

| Suite | Flag | What it proves |
|-------|------|----------------|
| `test_live_complete.py` | `RUN_LIVE_COMPLETE=1` | Full path + dashboard/auth/approve/regenerate + JSON artifacts |
| `test_live_graph_smoke.py` | `RUN_LIVE_GRAPH=1` | Entra token + mailbox read |
| `test_live_claude_triage.py` | `RUN_LIVE_CLAUDE=1` | Graph → PII scrub → Haiku |
| `test_live_e2e_pipeline.py` | `RUN_LIVE_E2E=1` | Graph → Redis → DB → triage → draft → Slack |
| `test_live_embedding_e2e.py` | `RUN_LIVE_EMBEDDING_E2E=1` | Embeddings, similarity, tone memory, Flow B, approve |
| `test_samplelab_skill_logical.py` | `RUN_LIVE_SKILL=1` | Elise samplelab-rebilling: Haiku selection + Sonnet `read_skill_reference` |

Never commit artifact JSON — it may contain email content.
