"""Local offload helper for webhook background work.

Locally wraps FastAPI BackgroundTasks. Keep call sites on this function so the
transport can later switch to SQS/ARQ without touching routes.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import BackgroundTasks


def enqueue(
    background_tasks: BackgroundTasks,
    coro_func: Callable[..., Coroutine[Any, Any, Any]],
    *args: Any,
    **kwargs: Any,
) -> None:
    """Schedule coroutine work after the HTTP response is sent (local)."""
    background_tasks.add_task(coro_func, *args, **kwargs)
