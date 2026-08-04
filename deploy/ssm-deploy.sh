#!/bin/bash
# Runs on the EC2 instance via `aws ssm send-command` (AWS-RunShellScript),
# which executes as root — hence the safe.directory workaround below.
set -euo pipefail

REPO_DIR="/home/ssm-user/inbox-triage-automation"
BRANCH="dev"

git config --global --add safe.directory "$REPO_DIR"
cd "$REPO_DIR"

git fetch origin "$BRANCH"
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
docker image prune -f
