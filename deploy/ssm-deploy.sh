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
BRANCH="${BRANCH:-dev}"
SHA="${DEPLOY_SHA:?DEPLOY_SHA required}"

git config --global --add safe.directory "$REPO_DIR"
cd "$REPO_DIR"

git fetch origin "$BRANCH"
# Ensure the CI-tested SHA is on the deploy branch tip history.
if ! git merge-base --is-ancestor "$SHA" "origin/$BRANCH"; then
  echo "DEPLOY_SHA $SHA is not an ancestor of origin/$BRANCH" >&2
  exit 1
fi
git checkout --detach "$SHA"

docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
docker image prune -f
