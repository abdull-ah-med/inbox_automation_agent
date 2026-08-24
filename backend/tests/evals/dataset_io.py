"""Load curated eval cases and write score artifacts."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVALS_ROOT = Path(__file__).resolve().parent
DATASET_V1 = EVALS_ROOT / "dataset" / "v1"
CHAT_DATASET_V1 = EVALS_ROOT / "dataset" / "chat_v1"
ARTIFACTS_ROOT = EVALS_ROOT.parent / "artifacts" / "evals"


def load_case(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def list_v1_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(DATASET_V1.glob("*.json")):
        case = load_case(path)
        case["_source_path"] = str(path)
        cases.append(case)
    return cases


def is_draft_eval_case(case: dict[str, Any]) -> bool:
    """Draft-pipeline gold has a subject + body. Chat gold uses question + hits."""
    return bool(case.get("subject")) and bool(case.get("body"))


def cases_for_suite(suite: str) -> list[dict[str, Any]]:
    tag = suite.upper()
    return [
        c
        for c in list_v1_cases()
        if is_draft_eval_case(c) and tag in {t.upper() for t in c.get("suite_tags", [])}
    ]


def list_chat_v1_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(CHAT_DATASET_V1.glob("*.json")):
        case = load_case(path)
        case["_source_path"] = str(path)
        cases.append(case)
    return cases


def chat_cases_for_suite(suite: str) -> list[dict[str, Any]]:
    tag = suite.upper()
    return [
        c for c in list_chat_v1_cases() if tag in {t.upper() for t in c.get("suite_tags", [])}
    ]


def new_run_dir(prefix: str = "run") -> Path:
    run_id = f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    path = ARTIFACTS_ROOT / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
