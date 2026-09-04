#!/bin/bash
# Send pinned deploy script to EC2 via SSM and poll until completion.
#
# Required env: INSTANCE_ID, DEPLOY_SHA
# Optional env: BRANCH (default dev), SSM_POLL_ITERATIONS (default 180),
#               SSM_POLL_INTERVAL_SECONDS (default 10)
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:?INSTANCE_ID required}"
DEPLOY_SHA="${DEPLOY_SHA:?DEPLOY_SHA required}"
BRANCH="${BRANCH:-dev}"
GITHUB_TOKEN="${GITHUB_TOKEN:?GITHUB_TOKEN required}"
SSM_POLL_ITERATIONS="${SSM_POLL_ITERATIONS:-180}"
SSM_POLL_INTERVAL_SECONDS="${SSM_POLL_INTERVAL_SECONDS:-10}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

TOKEN_PARAM="/inbox-triage-automation/deploy/${DEPLOY_SHA}/github-token"

aws ssm put-parameter \
  --name "$TOKEN_PARAM" \
  --type SecureString \
  --value "$GITHUB_TOKEN" \
  --overwrite >/dev/null
trap 'aws ssm delete-parameter --name "$TOKEN_PARAM" >/dev/null || true' EXIT

{
  echo "export DEPLOY_SHA='${DEPLOY_SHA}'"
  echo "export BRANCH='${BRANCH}'"
  echo "export TOKEN_PARAM='${TOKEN_PARAM}'"
  cat "${REPO_ROOT}/deploy/ssm-deploy.sh"
} > /tmp/ssm-deploy-pinned.sh

COMMANDS_JSON=$(bash "${REPO_ROOT}/deploy/ssm-commands-json.sh" /tmp/ssm-deploy-pinned.sh)

COMMAND_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --comment "Deploy inbox-triage-automation @ ${DEPLOY_SHA}" \
  --parameters "{\"commands\":${COMMANDS_JSON}}" \
  --query "Command.CommandId" --output text)

echo "SSM command: $COMMAND_ID"

STATUS="Pending"
for _ in $(seq 1 "$SSM_POLL_ITERATIONS"); do
  STATUS=$(aws ssm get-command-invocation \
    --command-id "$COMMAND_ID" \
    --instance-id "$INSTANCE_ID" \
    --query "Status" --output text 2>/dev/null || echo "Pending")

  case "$STATUS" in
    Success) break ;;
    Failed|Cancelled|TimedOut)
      echo "Deploy failed with status: $STATUS"
      aws ssm get-command-invocation \
        --command-id "$COMMAND_ID" \
        --instance-id "$INSTANCE_ID" \
        --query "StandardErrorContent" --output text
      exit 1
      ;;
    *) sleep "$SSM_POLL_INTERVAL_SECONDS" ;;
  esac
done

if [ "$STATUS" != "Success" ]; then
  echo "Timed out waiting for deploy command to finish (last status: $STATUS)"
  exit 1
fi

echo "Deploy succeeded."
aws ssm get-command-invocation \
  --command-id "$COMMAND_ID" \
  --instance-id "$INSTANCE_ID" \
  --query "StandardOutputContent" --output text
