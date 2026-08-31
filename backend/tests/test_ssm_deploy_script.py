"""Deploy script must encode as one SSM command and use extended polling."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "ssm-deploy.sh"
GITHUB_ACTIONS_DEPLOY = REPO_ROOT / "deploy" / "github-actions-ssm-deploy.sh"


def test_ssm_deploy_uses_safe_git_and_postgres_guard() -> None:
    text = DEPLOY_SCRIPT.read_text()
    assert "safe.directory=${REPO_DIR}" in text
    assert "postgres_data volume not found" in text
    assert "up --build -d backend frontend" in text


def test_github_actions_ssm_deploy_uses_extended_poll() -> None:
    text = GITHUB_ACTIONS_DEPLOY.read_text()
    assert "SSM_POLL_ITERATIONS" in text
    assert "180" in text
    assert "GITHUB_TOKEN" in text
