#!/bin/bash
# Fail unless required CI check runs succeeded for a commit (manual deploy gate).
#
# Usage: verify-ci-checks.sh <sha> <owner/repo>
# Requires: gh CLI authenticated (GITHUB_TOKEN in CI).
set -euo pipefail

SHA="${1:?commit sha required}"
REPO="${2:?owner/repo required}"

required_checks=(
  "Backend (lint + test)"
  "Frontend (lint + typecheck + test + build)"
  "Semgrep CE"
)

fail=0
for name in "${required_checks[@]}"; do
  mapfile -t rows < <(
    gh api "repos/${REPO}/commits/${SHA}/check-runs" \
      --paginate \
      -q ".check_runs[] | select(.name == \"${name}\") | \"\(.status)\t\(.conclusion // \"\")\"" \
      2>/dev/null || true
  )
  if [ "${#rows[@]}" -eq 0 ]; then
    echo "Required check '${name}' not found for ${SHA}"
    fail=1
    continue
  fi
  IFS=$'\t' read -r status conclusion <<< "${rows[0]}"
  if [ "$status" != "completed" ]; then
    echo "Required check '${name}' not completed (status=${status})"
    fail=1
    continue
  fi
  if [ "$conclusion" != "success" ]; then
    echo "Required check '${name}' conclusion: ${conclusion}"
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "CI verification failed for ${SHA} — deploy blocked."
  exit 1
fi

echo "All required CI checks passed for ${SHA}."
