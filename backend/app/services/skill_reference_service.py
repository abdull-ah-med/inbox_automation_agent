"""Load bundled skill reference/asset content for the draft tool loop."""

from __future__ import annotations

import base64
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import get_session_factory
from app.repositories import skill_files_repo

logger = structlog.get_logger(__name__)

MAX_ASSET_BYTES = 200 * 1024
PER_DRAFT_REFERENCE_BUDGET = 50 * 1024

SkillReferenceLoader = Callable[[uuid.UUID, str], Awaitable[dict[str, Any]]]


def _format_payload(*, mime_type: str, content: bytes) -> str:
    if mime_type.startswith("text/") or mime_type in {
        "application/json",
        "application/yaml",
        "application/xml",
    }:
        return content.decode("utf-8", errors="replace")
    encoded = base64.standard_b64encode(content).decode("ascii")
    return f"[base64 mime={mime_type} bytes={len(content)}]\n{encoded}"


async def load_skill_reference(
    *,
    skill_id: uuid.UUID,
    path: str,
    active_skill_ids: set[uuid.UUID],
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    remaining_budget: int = PER_DRAFT_REFERENCE_BUDGET,
) -> dict[str, Any]:
    """Return a tool_result-shaped payload for read_skill_reference.

    Never touches the filesystem. Opens a short-lived DB session per call.
    """
    if skill_id not in active_skill_ids:
        return {
            "content": f"Skill {skill_id} is not active for this draft",
            "is_error": True,
            "bytes": 0,
        }
    if remaining_budget <= 0:
        return {
            "content": "[reference truncated - file exceeds per-draft budget]",
            "is_error": False,
            "bytes": 0,
            "truncated": True,
        }

    factory = session_factory or get_session_factory()
    async with factory() as session:
        row = await skill_files_repo.get_by_path(
            session,
            skill_id=skill_id,
            relative_path=path,
        )
    if row is None:
        return {
            "content": f"Reference not found: {path}",
            "is_error": True,
            "bytes": 0,
        }
    if row.kind == "asset" and row.size_bytes > MAX_ASSET_BYTES:
        return {
            "content": (
                f"Asset exceeds {MAX_ASSET_BYTES} byte limit "
                f"({row.size_bytes} bytes): {path}"
            ),
            "is_error": True,
            "bytes": 0,
        }

    payload = _format_payload(mime_type=row.mime_type, content=row.content)
    encoded_bytes = len(payload.encode("utf-8"))
    if encoded_bytes > remaining_budget:
        return {
            "content": "[reference truncated - file exceeds per-draft budget]",
            "is_error": False,
            "bytes": 0,
            "truncated": True,
        }
    return {
        "content": payload,
        "is_error": False,
        "bytes": encoded_bytes,
        "mime_type": row.mime_type,
        "kind": row.kind,
    }


def make_reference_loader(
    *,
    active_skill_ids: set[uuid.UUID],
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[SkillReferenceLoader, list[dict[str, Any]]]:
    """Build a budget-tracking loader and a shared tool_calls log list."""
    tool_calls: list[dict[str, Any]] = []
    remaining = PER_DRAFT_REFERENCE_BUDGET

    async def _loader(skill_id: uuid.UUID, path: str) -> dict[str, Any]:
        nonlocal remaining
        result = await load_skill_reference(
            skill_id=skill_id,
            path=path,
            active_skill_ids=active_skill_ids,
            session_factory=session_factory,
            remaining_budget=remaining,
        )
        used = int(result.get("bytes") or 0)
        remaining = max(0, remaining - used)
        tool_calls.append(
            {
                "skill_id": str(skill_id),
                "path": path,
                "bytes": used,
                "is_error": bool(result.get("is_error")),
                "truncated": bool(result.get("truncated")),
            }
        )
        return result

    return _loader, tool_calls
