"""Gate llm_eval suites behind RUN_LLM_EVAL and optional [eval] deps."""

from __future__ import annotations

import pytest

from tests.live_helpers import env_flag


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if env_flag("RUN_LLM_EVAL"):
        return
    skip = pytest.mark.skip(reason="Set RUN_LLM_EVAL=1 to run DeepEval/RAGAS LLM evals")
    for item in items:
        if "llm_eval" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def eval_deps_available() -> bool:
    try:
        import deepeval  # noqa: F401
        import ragas  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture
def require_eval_deps(eval_deps_available: bool) -> None:
    if not eval_deps_available:
        pytest.skip('Install eval deps: pip install -e ".[eval]"')
