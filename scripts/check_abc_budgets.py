#!/usr/bin/env python3
"""Enforce LOC-per-file and ABC magnitude budgets (see scripts/lint-budgets.toml).

Cyclomatic complexity is gated by oxlint (`complexity`) and ruff (`C901`).
This script covers the two metrics those tools do not share:
  - physical lines of code per file
  - ABC magnitude per function: sqrt(A^2 + B^2 + C^2)

Usage:
  python3 scripts/check_abc_budgets.py --backend
  python3 scripts/check_abc_budgets.py --frontend
  python3 scripts/check_abc_budgets.py --all
"""

from __future__ import annotations

import argparse
import ast
import math
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11
    import tomli as tomllib  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[1]
BUDGETS_PATH = REPO_ROOT / "scripts" / "lint-budgets.toml"

BACKEND_EXCLUDE_PARTS = {
    "_vendor",
    "migrations",
    ".venv",
    "__pycache__",
}
FRONTEND_EXCLUDE_PARTS = {
    "node_modules",
    ".next",
    "out",
    "build",
    "components/ui",
}


def load_budgets() -> dict:
    with BUDGETS_PATH.open("rb") as fh:
        return tomllib.load(fh)


def physical_loc(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def physical_loc_ts(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="ignore")
    count = 0
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if in_block:
            if "*/" in line:
                in_block = False
            continue
        if line.startswith("/*"):
            if "*/" not in line:
                in_block = True
            continue
        if line.startswith("//"):
            continue
        count += 1
    return count


class AbcVisitor(ast.NodeVisitor):
    """Jerry Fitzpatrick ABC metric for Python (assignments/branches/conditions)."""

    def __init__(self) -> None:
        self.a = 0
        self.b = 0
        self.c = 0

    def visit_Assign(self, node: ast.Assign) -> None:
        self.a += 1
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.a += 1
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.a += 1
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.b += 1
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.c += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.c += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.b += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.b += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.b += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.c += max(len(node.values) - 1, 0)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        self.c += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.c += 1
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.c += 1
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.b += 1
        self.c += len(node.ifs)
        self.generic_visit(node)


def abc_magnitude(a: int, b: int, c: int) -> float:
    return math.sqrt(a * a + b * b + c * c)


def python_function_abcs(path: Path) -> list[tuple[str, int, float]]:
    source = path.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    results: list[tuple[str, int, float]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        visitor = AbcVisitor()
        for child in node.body:
            visitor.visit(child)
        mag = abc_magnitude(visitor.a, visitor.b, visitor.c)
        results.append((node.name, node.lineno, mag))
    return results


# Lightweight TS/JS ABC via regex/token heuristics (no extra deps).
# Counts are intentionally approximate and conservative for CI gating.
_ASSIGN_RE = re.compile(
    r"(?<![!=<>])=(?!=)|(\+\+|--)|(\+=|-=|\*=|/=|%=|\|=|&=|\^=)"
)
_BRANCH_RE = re.compile(
    r"\b(if|else if|for|while|switch|catch|case|\?\.|\?\?)\b|\?[^?]|&&|\|\|"
)
_CALL_RE = re.compile(r"\b[A-Za-z_$][\w$]*\s*\(")


def ts_function_spans(text: str) -> list[tuple[str, int, str]]:
    """Return (name, start_line, body) for top-level-ish function-like blocks."""
    lines = text.splitlines()
    spans: list[tuple[str, int, str]] = []
    # Match function declarations / const arrows / methods roughly.
    header = re.compile(
        r"^(?:export\s+)?(?:async\s+)?(?:function\s+([A-Za-z_$][\w$]*)|"
        r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|"
        r"(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{)"
    )
    i = 0
    while i < len(lines):
        m = header.match(lines[i].strip())
        if not m:
            i += 1
            continue
        name = next(g for g in m.groups() if g)
        start = i + 1
        # Find body start
        chunk = "\n".join(lines[i:])
        brace = chunk.find("{")
        if brace < 0:
            i += 1
            continue
        depth = 0
        end_idx = None
        for idx, ch in enumerate(chunk[brace:], start=brace):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_idx = idx
                    break
        if end_idx is None:
            i += 1
            continue
        body = chunk[brace : end_idx + 1]
        spans.append((name, start, body))
        consumed = body.count("\n")
        i += max(consumed, 1)
    return spans


def ts_abc_for_body(body: str) -> float:
    # Strip strings/comments lightly
    stripped = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    stripped = re.sub(r"//.*?$", "", stripped, flags=re.M)
    stripped = re.sub(r"'(?:\\.|[^\\'])*'|\"(?:\\.|[^\\\"])*\"|`(?:\\.|[^\\`])*`", "", stripped)
    a = len(_ASSIGN_RE.findall(stripped))
    # Branches include conditions; calls are B in Fitzpatrick ABC for some langs.
    # We map: assignments=A, calls+loops=B, conditionals=C.
    calls = len(_CALL_RE.findall(stripped))
    branches = len(re.findall(r"\b(for|while|switch|catch)\b", stripped))
    conditions = len(
        re.findall(r"\b(if|else if|case)\b|\?[^?]|&&|\|\||\?\?", stripped)
    )
    b = calls + branches
    c = conditions
    return abc_magnitude(a, b, c)


def check_frontend(budgets: dict) -> list[str]:
    cfg = budgets["frontend"]
    test_cfg = budgets["frontend"].get("tests", cfg)
    root = REPO_ROOT / "frontend" / "web" / "src"
    errors: list[str] = []

    for path in sorted(root.rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if any(part in FRONTEND_EXCLUDE_PARTS for part in path.parts):
            continue
        rel = path.relative_to(REPO_ROOT)
        is_test = (
            path.name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
            or "test" in path.parts
        )
        active = test_cfg if is_test else cfg
        loc = physical_loc_ts(path)
        max_loc = int(active["max_file_loc"])
        if loc > max_loc:
            errors.append(f"LOC {loc} > {max_loc}: {rel}")

        # JSX call density inflates ABC; gate ABC on .ts modules only.
        if path.suffix != ".ts":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        max_abc = float(active["max_abc"])
        for name, lineno, body in ts_function_spans(text):
            mag = ts_abc_for_body(body)
            if mag > max_abc:
                errors.append(f"ABC {mag:.1f} > {max_abc}: {rel}:{lineno} {name}")
    return errors


def check_backend(budgets: dict) -> list[str]:
    cfg = budgets["backend"]
    test_cfg = budgets["backend"].get("tests", cfg)

    root = REPO_ROOT / "backend" / "app"
    tests_root = REPO_ROOT / "backend" / "tests"
    errors: list[str] = []

    for path in sorted(root.rglob("*.py")):
        if any(part in BACKEND_EXCLUDE_PARTS for part in path.parts):
            continue
        loc = physical_loc(path)
        max_loc = int(cfg["max_file_loc"])
        if loc > max_loc:
            errors.append(f"LOC {loc} > {max_loc}: {path.relative_to(REPO_ROOT)}")
        for name, lineno, mag in python_function_abcs(path):
            if mag > float(cfg["max_abc"]):
                errors.append(
                    f"ABC {mag:.1f} > {cfg['max_abc']}: "
                    f"{path.relative_to(REPO_ROOT)}:{lineno} {name}"
                )

    if tests_root.exists():
        max_loc = int(test_cfg.get("max_file_loc", cfg["max_file_loc"]))
        max_abc = float(test_cfg.get("max_abc", cfg["max_abc"]))
        for path in sorted(tests_root.rglob("*.py")):
            # Opt-in live E2E scripts are intentionally long happy-paths.
            if path.name.startswith("test_live_"):
                continue
            loc = physical_loc(path)
            if loc > max_loc:
                errors.append(f"LOC {loc} > {max_loc}: {path.relative_to(REPO_ROOT)}")
            for name, lineno, mag in python_function_abcs(path):
                if mag > max_abc:
                    errors.append(
                        f"ABC {mag:.1f} > {max_abc}: "
                        f"{path.relative_to(REPO_ROOT)}:{lineno} {name}"
                    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", action="store_true")
    parser.add_argument("--frontend", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if not (args.backend or args.frontend or args.all):
        args.all = True

    budgets = load_budgets()
    errors: list[str] = []
    if args.all or args.backend:
        errors.extend(check_backend(budgets))
    if args.all or args.frontend:
        errors.extend(check_frontend(budgets))

    if errors:
        print(f"Budget gate failed ({len(errors)} violation(s)):", file=sys.stderr)
        for err in errors[:200]:
            print(f"  {err}", file=sys.stderr)
        if len(errors) > 200:
            print(f"  ... and {len(errors) - 200} more", file=sys.stderr)
        return 1

    targets = []
    if args.all or args.backend:
        targets.append("backend")
    if args.all or args.frontend:
        targets.append("frontend")
    print(f"Budget gate passed ({', '.join(targets)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
