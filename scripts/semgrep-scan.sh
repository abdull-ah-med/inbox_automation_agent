#!/usr/bin/env bash
# Canonical Semgrep CE scan — keep in sync with .github/workflows/ci-cd.yml.
# Docs:
#   https://docs.semgrep.dev/customize-semgrep-ce
#   https://docs.semgrep.dev/semgrep-ci/sample-ci-configs
#   https://semgrep.dev/docs/getting-started/cli
#
# Note: `--config auto` requires metrics (Semgrep refuses auto + --metrics=off).
# We pin explicit registry packs instead so local/CI stay offline-friendly and
# deterministic.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SEMGREP_BIN="${SEMGREP_BIN:-semgrep}"

# --error: fail the process when findings exist (default scan exits 0).
# --metrics=off: no registry telemetry for local/CI OSS scans.
exec "$SEMGREP_BIN" scan \
  --config p/default \
  --config p/python \
  --config p/fastapi \
  --config p/javascript \
  --config p/react \
  --config p/typescript \
  --error \
  --metrics=off \
  "$@"
