#!/bin/bash
# Fail unless the canonical CI/CD workflow succeeded for a commit (manual deploy gate).
#
# Duplicate check-run *names* from other workflows must not satisfy this gate.
# We pin .github/workflows/ci-cd.yml via the Actions runs API and require every
# matching run — and each required job inside those runs — completed+success.
#
# Usage: verify-ci-checks.sh <sha> <owner/repo>
# Requires: gh CLI authenticated (GITHUB_TOKEN in CI).
set -euo pipefail

SHA="${1:?commit sha required}"
REPO="${2:?owner/repo required}"
CANONICAL_WORKFLOW=".github/workflows/ci-cd.yml"

required_jobs=(
  "Backend (lint + test)"
  "Frontend (lint + typecheck + test + build)"
  "Semgrep CE"
)

mapfile -t runs < <(
  gh api "repos/${REPO}/actions/runs?head_sha=${SHA}" \
    --paginate \
    -q ".workflow_runs[] | select(.path == \"${CANONICAL_WORKFLOW}\") | \"\(.id)\t\(.status)\t\(.conclusion // \"\")\"" \
    2>/dev/null || true
)

if [ "${#runs[@]}" -eq 0 ]; then
  echo "Canonical workflow ${CANONICAL_WORKFLOW} has no runs for ${SHA}"
  exit 1
fi

fail=0
run_ids=()
for row in "${runs[@]}"; do
  IFS=$'\t' read -r run_id status conclusion <<< "${row}"
  run_ids+=("${run_id}")
  if [ "${status}" != "completed" ]; then
    echo "Canonical workflow run ${run_id} not completed (status=${status})"
    fail=1
    continue
  fi
  if [ "${conclusion}" != "success" ]; then
    echo "Canonical workflow run ${run_id} conclusion: ${conclusion}"
    fail=1
  fi
done

for name in "${required_jobs[@]}"; do
  mapfile -t jobs < <(
    for run_id in "${run_ids[@]}"; do
      gh api "repos/${REPO}/actions/runs/${run_id}/jobs" \
        --paginate \
        -q ".jobs[] | select(.name == \"${name}\") | \"\(.status)\t\(.conclusion // \"\")\"" \
        2>/dev/null || true
    done
  )
  if [ "${#jobs[@]}" -eq 0 ]; then
    echo "Required job '${name}' not found on ${CANONICAL_WORKFLOW} for ${SHA}"
    fail=1
    continue
  fi
  for job_row in "${jobs[@]}"; do
    IFS=$'\t' read -r status conclusion <<< "${job_row}"
    if [ "${status}" != "completed" ]; then
      echo "Required job '${name}' not completed (status=${status})"
      fail=1
      continue
    fi
    if [ "${conclusion}" != "success" ]; then
      echo "Required job '${name}' conclusion: ${conclusion}"
      fail=1
    fi
  done
done

if [ "$fail" -ne 0 ]; then
  echo "CI verification failed for ${SHA} — deploy blocked."
  exit 1
fi

echo "All required CI checks passed for ${SHA}."
