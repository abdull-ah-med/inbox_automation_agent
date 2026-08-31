"""Deploy script must work under SSM RunShellScript (root, no HOME)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "ssm-deploy.sh"


def test_git_global_config_fails_when_home_unset() -> None:
    """SSM AWS-RunShellScript runs as root without HOME; global git config breaks."""
    result = subprocess.run(
        [
            "env",
            "-i",
            f"PATH={os.environ['PATH']}",
            "bash",
            "-c",
            "git config --global --add safe.directory /tmp/example",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "HOME not set" in result.stderr


def test_ssm_deploy_uses_safe_directory_without_global_git_config() -> None:
    script = DEPLOY_SCRIPT.read_text()
    executable_lines = [
        line.strip()
        for line in script.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert not any(line.startswith("git config --global") for line in executable_lines)
    assert any("safe.directory=${REPO_DIR}" in line for line in executable_lines)


def test_ssm_deploy_preserves_database_and_only_restarts_app_services() -> None:
    script = DEPLOY_SCRIPT.read_text()
    executable_lines = [
        line.strip()
        for line in script.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert not any("down -v" in line for line in executable_lines)
    assert not any("volume prune" in line for line in executable_lines)
    assert any("up --build -d backend frontend" in line for line in executable_lines)
    assert any("postgres_data volume not found" in line for line in executable_lines)
    assert any("x-access-token:${GITHUB_TOKEN}@github.com/" in line for line in executable_lines)
