"""AWS-RunShellScript must receive the deploy script as one command.

split("\\n") would make #!/bin/bash its own commands[] entry, which the
plugin executes as a standalone command (not a shebang).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENCODER = REPO_ROOT / "deploy" / "ssm-commands-json.sh"

# Hand-written fixture: shebang + a compound command that is only valid
# when kept as one script, not as separate RunShellScript lines.
_SCRIPT = """\
#!/bin/bash
set -euo pipefail
if ! true; then
  echo fail >&2
  exit 1
fi
"""


def test_encoder_keeps_shebang_inside_single_command(tmp_path: Path) -> None:
    script = tmp_path / "deploy.sh"
    script.write_text(_SCRIPT)

    result = subprocess.run(
        ["bash", str(ENCODER), str(script)],
        check=True,
        capture_output=True,
        text=True,
    )
    commands = json.loads(result.stdout)

    assert isinstance(commands, list)
    assert len(commands) == 1
    assert commands[0].startswith("#!/bin/bash\n")
    assert "if ! true; then" in commands[0]
    assert "\n" in commands[0]
    assert commands[0] != "#!/bin/bash"


def test_production_ssm_deploy_script_encodes_as_one_command() -> None:
    script = REPO_ROOT / "deploy" / "ssm-deploy.sh"
    result = subprocess.run(
        ["bash", str(ENCODER), str(script)],
        check=True,
        capture_output=True,
        text=True,
    )
    commands = json.loads(result.stdout)

    assert len(commands) == 1
    assert commands[0].startswith("#!/bin/bash\n")
    assert "set -euo pipefail" in commands[0]
    assert commands[0] != "#!/bin/bash"
