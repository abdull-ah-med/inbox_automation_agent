"""CI-safe samplelab-rebilling skill archive for unit/logical tests.

``misc/`` is gitignored, so the real Elise zip is absent on CI checkouts.
Tests that need Anthropic-packaged structure + progressive-disclosure cues
build this in-memory archive instead. Prefer the real misc zip when present
(local live runs) via :func:`load_samplelab_rebilling_zip_bytes`.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

MISC_FIXTURE_ZIP = (
    Path(__file__).resolve().parents[3] / "misc" / "samplelab-rebilling-skill.zip"
)

SKILL_NAME = "samplelab-rebilling"

SKILL_DESCRIPTION = (
    "Use when Elise must process the samplelab invoice and rebill Harmeyer "
    "departments from Invoicing_Detail exports."
)

# Body >400 chars; "Step 7" only after the discovery head so embedding excludes it.
SKILL_BODY = """\
# SampleLab rebilling

Load references/output_format.md for column layout before drafting.

Pad discovery head so late workflow steps stay out of Level-1 embedding.
""" + ("x" * 280) + """

## FULL MONTHLY WORKFLOW

Step 1 — Load Invoice detail from SampleLabVendor / SampleLab.
Step 2 — Match Harmeyer departments via references/harmeyer_departments.csv.
Step 3 — Apply references/client_rules.md spelling and tab rules.
Step 4 — Build detail files per department.
Step 5 — Validate SSN and dates against output format.
Step 6 — Draft reply summarizing rebills.
Step 7 — Confirm with Elise before send (read-only system never sends).
"""

CLIENT_RULES = """\
# Client rules

Correct spelling: Trolinder (not Trolinder misspellings).
Harmeyer Transport departments: Tank / HDV / Maintenance / Admin.
"""

OUTPUT_FORMAT = """\
# Output format

Required columns: Collection Date, Employee Name, SSN Last 4, Department.
"""

HARMEYER_CSV = """\
first,last,department
Randy,Alvis,Maintenance
Pat,Trolinder,Tank
"""


def build_samplelab_rebilling_zip_bytes() -> bytes:
    """Return a valid Anthropic-packaged zip matching logical-test assertions."""
    root = SKILL_NAME
    skill_md = (
        f"---\nname: {SKILL_NAME}\ndescription: {SKILL_DESCRIPTION}\n---\n"
        f"{SKILL_BODY}"
    )
    members = {
        f"{root}/SKILL.md": skill_md,
        f"{root}/references/client_rules.md": CLIENT_RULES,
        f"{root}/references/output_format.md": OUTPUT_FORMAT,
        f"{root}/references/harmeyer_departments.csv": HARMEYER_CSV,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in members.items():
            zf.writestr(path, content.encode("utf-8"))
    return buf.getvalue()


def load_samplelab_rebilling_zip_bytes() -> bytes:
    """Prefer real misc zip when present; otherwise build the CI fixture."""
    if MISC_FIXTURE_ZIP.is_file():
        return MISC_FIXTURE_ZIP.read_bytes()
    return build_samplelab_rebilling_zip_bytes()
