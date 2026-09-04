#!/bin/bash
# Runs on the EC2 instance via `aws ssm send-command` (AWS-RunShellScript),
# which executes as root — hence the safe.directory workaround below.
#
# Requires DEPLOY_SHA (CI-verified commit) exported by the workflow before this
# script body. Pins the checkout to that SHA so a race on branch tip cannot
# deploy unaudited code.
# Refs:
# - https://docs.github.com/en/actions/learn-github-actions/variables#default-environment-variables
# - https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html
set -euo pipefail

REPO_DIR="/home/ssm-user/inbox-triage-automation"
GITHUB_REPO="abdull-ah-med/inbox_automation_agent"
BRANCH="${BRANCH:-dev}"
SHA="${DEPLOY_SHA:?DEPLOY_SHA required}"
TOKEN_PARAM="${TOKEN_PARAM:?TOKEN_PARAM required}"
GITHUB_TOKEN="$(aws ssm get-parameter --name "$TOKEN_PARAM" --with-decryption --query Parameter.Value --output text)"

# SSM RunShellScript runs as root without HOME; avoid `git config --global`.
GIT=(git -c "safe.directory=${REPO_DIR}")
cd "$REPO_DIR"

# Job-scoped Actions token (no PAT). Embed in HTTPS URL — extraHeader alone fails
# when origin uses SSH or git prompts for credentials on headless SSM shells.
AUTH_FETCH_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"
"${GIT[@]}" fetch "$AUTH_FETCH_URL" "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}"
# Ensure the CI-tested SHA is on the deploy branch tip history.
if ! "${GIT[@]}" merge-base --is-ancestor "$SHA" "origin/$BRANCH"; then
  echo "DEPLOY_SHA $SHA is not an ancestor of origin/$BRANCH" >&2
  exit 1
fi
"${GIT[@]}" checkout --detach "$SHA"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

# Data safety: deploy must not wipe or recreate Postgres/Redis data stores.
# - Never `docker compose down -v` or `docker volume prune`.
# - Rebuild/restart app containers only; leave postgres/redis containers as-is.
# - Abort if the existing postgres_data volume is missing (no accidental fresh DB).
if ! docker volume ls -q --filter "name=postgres_data" | grep -q .; then
  echo "postgres_data volume not found — aborting (refusing fresh database)" >&2
  exit 1
fi

"${COMPOSE[@]}" up --build -d backend frontend
docker image prune -f
