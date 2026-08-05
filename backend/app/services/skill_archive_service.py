"""Import Claude Agent Skills (.zip / .skill) into the skills table.

Packaging rules follow Anthropic's Agent Skills spec:
https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
https://claude.com/docs/skills/how-to

- ZIP must contain a single root folder (not files at archive root)
- Root folder name must match YAML ``name``
- SKILL.md requires name + description frontmatter
- references/ and assets/ are imported; scripts/ is skipped (read-only constraint)
"""

from __future__ import annotations

import hashlib
import io
import mimetypes
import re
import zipfile
from typing import Any

import structlog
import yaml  # type: ignore[import-untyped]
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import (
    SkillAlreadyImportedError,
    SkillNameConflictError,
    SkillPackageTooLargeError,
    SkillPackagingError,
    SkillPathTraversalError,
)
from app.models.schemas.skill import ImportSkillResultSchema
from app.repositories import skill_repo
from app.services import skill_embedding_service

logger = structlog.get_logger(__name__)

MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 5 * 1024 * 1024

_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_RESERVED_NAMES = frozenset({"anthropic", "claude"})
_XML_TAG_RE = re.compile(r"<[^>]+>")

_TEXT_EXTENSIONS = frozenset(
    {
        ".md",
        ".txt",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".tsv",
        ".xml",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".py",
        ".sh",
    }
)


def _is_junk_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    if any(part == "__MACOSX" or part.startswith("._") for part in parts):
        return True
    return bool(parts) and parts[-1] in {".DS_Store", "Thumbs.db"}


def _normalize_member_path(name: str) -> str:
    # Replace backslashes; strip a single leading "./" prefix only (do NOT use
    # str.lstrip("./") — that treats characters as a set and eats "../").
    normalized = name.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _assert_safe_path(path: str) -> None:
    # Reject before and after normalization so leading "../" cannot be eaten.
    raw = path.replace("\\", "/")
    raw_parts = raw.split("/")
    if any(part in {".", ".."} for part in raw_parts):
        raise SkillPathTraversalError(f"Unsafe archive path: {path}")

    normalized = _normalize_member_path(path)
    if not normalized or normalized.startswith("/") or ":" in normalized.split("/")[0]:
        raise SkillPathTraversalError(f"Unsafe archive path: {path}")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SkillPathTraversalError(f"Unsafe archive path: {path}")


def _guess_mime(relative_path: str) -> str:
    guessed, _ = mimetypes.guess_type(relative_path)
    if guessed:
        return guessed
    lower = relative_path.lower()
    if lower.endswith(".md"):
        return "text/markdown"
    if lower.endswith(".csv"):
        return "text/csv"
    if lower.endswith((".yaml", ".yml")):
        return "application/yaml"
    return "application/octet-stream"


def _parse_skill_md(raw: bytes) -> tuple[dict[str, Any], str]:
    text = raw.decode("utf-8-sig", errors="replace")
    if not text.lstrip().startswith("---"):
        raise SkillPackagingError("SKILL.md must start with YAML frontmatter (---)")

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, flags=re.DOTALL)
    if match is None:
        raise SkillPackagingError("SKILL.md frontmatter is malformed")

    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillPackagingError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillPackagingError("SKILL.md frontmatter must be a YAML mapping")

    body = match.group(2).strip("\n")
    return meta, body


def _validate_frontmatter(
    meta: dict[str, Any],
    *,
    root_dir: str,
) -> tuple[str, str, dict[str, Any]]:
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not name.strip():
        raise SkillPackagingError("SKILL.md frontmatter requires a non-empty name")
    name = name.strip()
    if not _NAME_RE.fullmatch(name):
        raise SkillPackagingError(
            "Skill name must be lowercase letters, numbers, and hyphens only (max 64 chars)"
        )
    if name in _RESERVED_NAMES:
        raise SkillPackagingError(f"Skill name '{name}' is reserved")
    if name != root_dir:
        raise SkillPackagingError(
            f"Archive root folder '{root_dir}' must match skill name '{name}'"
        )

    if not isinstance(description, str) or not description.strip():
        raise SkillPackagingError("SKILL.md frontmatter requires a non-empty description")
    description = description.strip()
    if len(description) > 1024:
        raise SkillPackagingError("Skill description must be at most 1024 characters")
    if _XML_TAG_RE.search(description) or _XML_TAG_RE.search(name):
        raise SkillPackagingError("Skill name/description cannot contain XML tags")

    extras = {k: v for k, v in meta.items() if k not in {"name", "description"}}
    return name, description, extras


def _classify_member(
    relative_under_root: str,
) -> tuple[str | None, str | None]:
    """Return (kind, warning) for a path under the skill root (excluding SKILL.md)."""
    normalized = relative_under_root.replace("\\", "/")
    if normalized.startswith("references/"):
        if normalized == "references/" or normalized.endswith("/"):
            return None, None
        return "reference", None
    if normalized.startswith("assets/"):
        if normalized == "assets/" or normalized.endswith("/"):
            return None, None
        return "asset", None
    if normalized.startswith("scripts/"):
        return None, f"scripts/ skipped (read-only constraint): {normalized}"
    if normalized.endswith("/"):
        return None, None
    return None, f"Unsupported path skipped: {normalized}"


def parse_skill_archive(
    archive_bytes: bytes,
    *,
    original_filename: str | None = None,
) -> tuple[str, str, str, dict[str, Any], list[dict[str, object]], list[str]]:
    """Parse and validate an archive in memory.

    Returns:
        (name, description, body, raw_frontmatter, files, warnings)
    """
    _ = original_filename
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise SkillPackageTooLargeError(
            f"Archive exceeds {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB limit"
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        raise SkillPackagingError("File is not a valid zip archive") from exc

    with zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        if not infos:
            raise SkillPackagingError("Archive is empty")

        total_uncompressed = 0
        members: list[tuple[str, zipfile.ZipInfo]] = []
        seen_paths: set[str] = set()
        for info in infos:
            path = _normalize_member_path(info.filename)
            if _is_junk_path(path):
                continue
            _assert_safe_path(info.filename)
            path = _normalize_member_path(info.filename)
            if path in seen_paths:
                raise SkillPackagingError(f"Duplicate archive path: {path}")
            seen_paths.add(path)
            if info.file_size > MAX_SINGLE_FILE_BYTES:
                raise SkillPackageTooLargeError(
                    f"File exceeds {MAX_SINGLE_FILE_BYTES // (1024 * 1024)} MB limit: {path}"
                )
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise SkillPackageTooLargeError(
                    "Uncompressed archive exceeds 25 MB limit (zip bomb guard)"
                )
            members.append((path, info))

        if not members:
            raise SkillPackagingError("Archive contains no usable files")

        top_levels = {path.split("/", 1)[0] for path, _ in members}
        if any("/" not in path for path, _ in members):
            # Anthropic packaging: zip must contain the skill folder as root.
            raise SkillPackagingError(
                "The ZIP should contain the skill folder as its root "
                "(my-skill/SKILL.md), not files directly in the ZIP root"
            )
        if len(top_levels) != 1:
            raise SkillPackagingError(
                "Archive must contain exactly one root skill folder"
            )
        root_dir = next(iter(top_levels))

        skill_md_path = f"{root_dir}/SKILL.md"
        skill_md_info = next((info for path, info in members if path == skill_md_path), None)
        if skill_md_info is None:
            # Case-insensitive fallback for Skill.md
            skill_md_info = next(
                (
                    info
                    for path, info in members
                    if path.lower() == skill_md_path.lower()
                ),
                None,
            )
            if skill_md_info is None:
                raise SkillPackagingError(f"Missing {skill_md_path}")

        skill_raw = zf.read(skill_md_info)
        meta, body = _parse_skill_md(skill_raw)
        name, description, extras = _validate_frontmatter(meta, root_dir=root_dir)

        files: list[dict[str, object]] = []
        warnings: list[str] = []
        for path, info in members:
            if path.lower() == skill_md_path.lower():
                continue
            relative = path[len(root_dir) + 1 :]
            kind, warning = _classify_member(relative)
            if warning:
                warnings.append(warning)
            if kind is None:
                continue
            payload = zf.read(info)
            files.append(
                {
                    "relative_path": relative,
                    "kind": kind,
                    "mime_type": _guess_mime(relative),
                    "content": payload,
                }
            )

        return name, description, body, extras, files, warnings


async def import_skill_archive(
    session: AsyncSession,
    *,
    archive_bytes: bytes,
    original_filename: str,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    overwrite: bool = False,
    category: str | None = "billing",
    always_apply: bool = False,
) -> ImportSkillResultSchema:
    """Validate, persist, and embed an imported Claude skill archive."""
    digest = hashlib.sha256(archive_bytes).hexdigest()
    existing_hash = await skill_repo.get_by_import_hash(session, digest)
    if existing_hash is not None and not overwrite:
        raise SkillAlreadyImportedError(
            f"Skill archive already imported as '{existing_hash.name}'",
            skill_id=existing_hash.id,
        )

    name, description, body, extras, files, warnings = parse_skill_archive(
        archive_bytes,
        original_filename=original_filename,
    )

    # Imported skills without a category become general unless always_apply.
    resolved_category = category
    if always_apply:
        resolved_category = None
    elif resolved_category is None:
        resolved_category = "general"

    try:
        skill, overwritten = await skill_repo.upsert_imported(
            session,
            name=name,
            description=description,
            content=body,
            category=resolved_category,
            always_apply=always_apply,
            imported_zip_sha256=digest,
            raw_frontmatter=extras or None,
            files=files,
            overwrite=overwrite or (existing_hash is not None),
        )
    except SkillNameConflictError:
        raise

    await skill_embedding_service.maybe_embed_skill(
        session,
        skill_id=skill.id,
        name=skill.name,
        description=skill.description,
        body=skill.content,
        settings=settings,
        openai_client=openai_client,
    )

    reference_files = [str(f["relative_path"]) for f in files if f["kind"] == "reference"]
    asset_files = [str(f["relative_path"]) for f in files if f["kind"] == "asset"]
    logger.info(
        "skill_archive_imported",
        skill_id=str(skill.id),
        name=name,
        reference_count=len(reference_files),
        asset_count=len(asset_files),
        warning_count=len(warnings),
        overwritten=overwritten,
    )
    return ImportSkillResultSchema(
        skill_id=skill.id,
        name=name,
        description=description,
        reference_files=reference_files,
        asset_files=asset_files,
        warnings=warnings,
        overwritten=overwritten,
    )
