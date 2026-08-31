"""Unit tests for skill_archive_service (in-memory zip packaging)."""

from __future__ import annotations

import hashlib
import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import (
    SkillDuplicateCandidatesError,
    SkillPackageTooLargeError,
    SkillPackagingError,
    SkillPathTraversalError,
)
from app.models.schemas.skill import ImportSkillResultSchema, SkillResponseSchema
from app.repositories.skill_repo import SkillSimilarityHit
from app.services import skill_archive_service
from app.services.skill_archive_service import (
    MAX_ARCHIVE_BYTES,
    MAX_SINGLE_FILE_BYTES,
    parse_skill_archive,
)
from tests.fixtures.samplelab_rebilling_archive import load_samplelab_rebilling_zip_bytes


def _zip_bytes(members: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in members.items():
            payload = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(path, payload)
    return buf.getvalue()


def _skill_md(
    *,
    name: str = "demo-skill",
    description: str = "A demo skill for unit tests",
    body: str = "Always bill the correct department.",
) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"


def _valid_archive(**extra: bytes | str) -> bytes:
    members: dict[str, bytes | str] = {
        "demo-skill/SKILL.md": _skill_md(),
        "demo-skill/references/client_rules.md": "# Client rules\nDo X.",
        "demo-skill/references/output_format.md": "# Format\nUse Y.",
        "demo-skill/assets/logo.txt": "logo-bytes",
    }
    members.update(extra)
    return _zip_bytes(members)


def test_parse_valid_archive_extracts_files_and_kinds() -> None:
    """Valid Anthropic-packaged zip yields name/body and typed file rows."""
    name, description, body, extras, files, warnings = parse_skill_archive(_valid_archive())
    assert name == "demo-skill"
    assert description.startswith("A demo skill")
    assert "Always bill" in body
    assert extras == {}
    assert warnings == []
    paths = {f["relative_path"]: f for f in files}
    assert set(paths) == {
        "references/client_rules.md",
        "references/output_format.md",
        "assets/logo.txt",
    }
    assert paths["references/client_rules.md"]["kind"] == "reference"
    assert paths["assets/logo.txt"]["kind"] == "asset"
    assert b"Do X." in paths["references/client_rules.md"]["content"]


def test_parse_fixture_samplelab_rebilling_zip() -> None:
    """SampleLab fixture unpacks to three reference files."""
    data = load_samplelab_rebilling_zip_bytes()
    name, description, body, _extras, files, warnings = parse_skill_archive(data)
    assert name == "samplelab-rebilling"
    assert description
    assert body.strip()
    assert warnings == []
    assert len(files) == 3
    assert all(f["kind"] == "reference" for f in files)
    paths = {f["relative_path"] for f in files}
    assert paths == {
        "references/client_rules.md",
        "references/output_format.md",
        "references/harmeyer_departments.csv",
    }


def test_rejects_skill_md_at_zip_root() -> None:
    """Files at archive root (no skill folder) are rejected."""
    raw = _zip_bytes({"SKILL.md": _skill_md()})
    with pytest.raises(SkillPackagingError, match="skill folder"):
        parse_skill_archive(raw)


def test_rejects_path_traversal() -> None:
    """Zip members with .. must raise SkillPathTraversalError."""
    raw = _zip_bytes(
        {
            "demo-skill/SKILL.md": _skill_md(),
            "demo-skill/references/../../etc/passwd": "root:x",
        }
    )
    with pytest.raises(SkillPathTraversalError):
        parse_skill_archive(raw)


def test_rejects_leading_dotdot_path() -> None:
    """Leading ../ must not be stripped into a seemingly safe path."""
    raw = _zip_bytes(
        {
            "../demo-skill/SKILL.md": _skill_md(),
        }
    )
    with pytest.raises(SkillPathTraversalError):
        parse_skill_archive(raw)


def test_rejects_duplicate_member_paths() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo-skill/SKILL.md", _skill_md())
        zf.writestr("demo-skill/references/a.md", "one")
        # Same normalized path again
        zf.writestr("demo-skill/references/a.md", "two")
    with pytest.raises(SkillPackagingError, match="Duplicate"):
        parse_skill_archive(buf.getvalue())


def test_rejects_archive_over_10mb() -> None:
    with pytest.raises(SkillPackageTooLargeError, match="10 MB"):
        parse_skill_archive(b"0" * (MAX_ARCHIVE_BYTES + 1))


def test_rejects_single_file_over_5mb() -> None:
    raw = _zip_bytes(
        {
            "demo-skill/SKILL.md": _skill_md(),
            "demo-skill/references/huge.md": b"x" * (MAX_SINGLE_FILE_BYTES + 1),
        }
    )
    with pytest.raises(SkillPackageTooLargeError, match="5 MB"):
        parse_skill_archive(raw)


@pytest.mark.parametrize(
    "bad_name",
    ["DemoSkill", "demo_skill", "claude", "anthropic", "has space"],
)
def test_rejects_invalid_name(bad_name: str) -> None:
    raw = _zip_bytes(
        {
            f"{bad_name}/SKILL.md": _skill_md(name=bad_name),
        }
    )
    with pytest.raises(SkillPackagingError):
        parse_skill_archive(raw)


def test_rejects_description_over_1024() -> None:
    raw = _zip_bytes(
        {
            "demo-skill/SKILL.md": _skill_md(description="d" * 1025),
        }
    )
    with pytest.raises(SkillPackagingError, match="1024"):
        parse_skill_archive(raw)


def test_scripts_folder_emits_warning_and_skips_rows() -> None:
    raw = _valid_archive(**{"demo-skill/scripts/run.py": "print('nope')"})
    _name, _desc, _body, _extras, files, warnings = parse_skill_archive(raw)
    assert any("scripts/" in w for w in warnings)
    assert all(not str(f["relative_path"]).startswith("scripts/") for f in files)
    assert len(files) == 3


@pytest.mark.asyncio
async def test_import_idempotent_same_hash_raises_without_overwrite(
    settings,
) -> None:
    """Re-uploading identical bytes raises SkillDuplicateCandidatesError."""
    raw = _valid_archive()
    digest = hashlib.sha256(raw).hexdigest()
    existing = MagicMock()
    existing.id = uuid4()
    existing.name = "demo-skill"

    with (
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_import_hash",
            AsyncMock(return_value=existing),
        ),
        pytest.raises(SkillDuplicateCandidatesError) as exc_info,
    ):
        await skill_archive_service.import_skill_archive(
            AsyncMock(),
            archive_bytes=raw,
            original_filename="demo-skill.zip",
            settings=settings,
            openai_client=None,
            overwrite=False,
        )
    assert len(exc_info.value.candidates) == 1
    assert exc_info.value.candidates[0]["id"] == existing.id
    assert exc_info.value.candidates[0]["name"] == "demo-skill"
    assert exc_info.value.candidates[0]["similarity"] == 1.0
    assert digest  # sanity: hash computed for comparison path


def _skill_response(*, skill_id=None, name: str = "demo-skill") -> SkillResponseSchema:
    return SkillResponseSchema.model_validate(
        {
            "id": skill_id or uuid4(),
            "name": name,
            "description": "A demo skill for unit tests",
            "content": "Always bill the correct department.",
            "category": "billing",
            "is_active": True,
            "source_kind": "imported",
            "imported_zip_sha256": "abc",
            "reference_file_count": 2,
            "asset_file_count": 1,
            "created_at": "2026-08-05T00:00:00Z",
            "updated_at": "2026-08-05T00:00:00Z",
        }
    )


@pytest.mark.asyncio
async def test_import_overwrite_replaces_content_and_files(settings) -> None:
    """overwrite=True replaces skill content + bundled files."""
    raw = _valid_archive()
    skill_id = uuid4()
    skill = _skill_response(skill_id=skill_id)
    skill = skill.model_copy(update={"imported_zip_sha256": hashlib.sha256(raw).hexdigest()})

    with (
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_import_hash",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_name",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.upsert_imported",
            AsyncMock(return_value=(skill, True)),
        ) as upsert,
        patch(
            "app.services.skill_archive_service.skill_embedding_service.maybe_embed_skill",
            AsyncMock(),
        ),
    ):
        result = await skill_archive_service.import_skill_archive(
            AsyncMock(),
            archive_bytes=raw,
            original_filename="demo-skill.zip",
            settings=settings,
            openai_client=None,
            overwrite=True,
            category="billing",
        )

    assert isinstance(result, ImportSkillResultSchema)
    assert result.overwritten is True
    assert result.skill_id == skill_id
    assert len(result.reference_files) == 2
    assert len(result.asset_files) == 1
    kwargs = upsert.await_args.kwargs
    assert kwargs["overwrite"] is True
    assert kwargs["name"] == "demo-skill"
    assert len(kwargs["files"]) == 3


@pytest.mark.asyncio
async def test_import_similar_without_overwrite_raises(settings) -> None:
    """Semantically similar skills block import until overwrite or rename."""
    raw = _valid_archive()
    hit_id = uuid4()
    client = MagicMock()
    settings = settings.model_copy(update={"openai_api_key": "sk-test"})

    with (
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_import_hash",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_name",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.embedding_service.embed_text",
            AsyncMock(return_value=[0.1] * 8),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.find_similar",
            AsyncMock(
                return_value=[
                    SkillSimilarityHit(id=hit_id, name="samplelab-rebilling", similarity=0.91)
                ]
            ),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.upsert_imported",
            AsyncMock(),
        ) as upsert,
        pytest.raises(SkillDuplicateCandidatesError) as exc_info,
    ):
        await skill_archive_service.import_skill_archive(
            AsyncMock(),
            archive_bytes=raw,
            original_filename="demo-skill.zip",
            settings=settings,
            openai_client=client,
            overwrite=False,
        )

    assert len(exc_info.value.candidates) == 1
    assert exc_info.value.candidates[0]["id"] == hit_id
    assert exc_info.value.candidates[0]["similarity"] == 0.91
    upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_overwrite_with_skill_id_targets_candidate(settings) -> None:
    """overwrite + overwrite_skill_id writes into that skill row."""
    raw = _valid_archive()
    target_id = uuid4()
    skill = _skill_response(skill_id=target_id, name="samplelab-rebilling")
    client = MagicMock()
    settings = settings.model_copy(update={"openai_api_key": "sk-test"})

    with (
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_import_hash",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_name",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.embedding_service.embed_text",
            AsyncMock(return_value=[0.1] * 8),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.find_similar",
            AsyncMock(
                return_value=[
                    SkillSimilarityHit(id=target_id, name="samplelab-rebilling", similarity=0.93)
                ]
            ),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.upsert_imported",
            AsyncMock(return_value=(skill, True)),
        ) as upsert,
        patch(
            "app.services.skill_archive_service.skill_embedding_service.maybe_embed_skill",
            AsyncMock(),
        ),
    ):
        result = await skill_archive_service.import_skill_archive(
            AsyncMock(),
            archive_bytes=raw,
            original_filename="demo-skill.zip",
            settings=settings,
            openai_client=client,
            overwrite=True,
            overwrite_skill_id=target_id,
        )

    assert result.overwritten is True
    assert upsert.await_args.kwargs["target_skill_id"] == target_id
    assert upsert.await_args.kwargs["overwrite"] is True


@pytest.mark.asyncio
async def test_import_overwrite_without_id_picks_top_candidate(settings) -> None:
    """overwrite=True without id selects the highest-similarity match."""
    raw = _valid_archive()
    top_id = uuid4()
    other_id = uuid4()
    skill = _skill_response(skill_id=top_id, name="samplelab-rebilling")
    client = MagicMock()
    settings = settings.model_copy(update={"openai_api_key": "sk-test"})

    with (
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_import_hash",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_name",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.embedding_service.embed_text",
            AsyncMock(return_value=[0.1] * 8),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.find_similar",
            AsyncMock(
                return_value=[
                    SkillSimilarityHit(id=top_id, name="samplelab-rebilling", similarity=0.94),
                    SkillSimilarityHit(id=other_id, name="other-skill", similarity=0.86),
                ]
            ),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.upsert_imported",
            AsyncMock(return_value=(skill, True)),
        ) as upsert,
        patch(
            "app.services.skill_archive_service.skill_embedding_service.maybe_embed_skill",
            AsyncMock(),
        ),
    ):
        await skill_archive_service.import_skill_archive(
            AsyncMock(),
            archive_bytes=raw,
            original_filename="demo-skill.zip",
            settings=settings,
            openai_client=client,
            overwrite=True,
        )

    assert upsert.await_args.kwargs["target_skill_id"] == top_id


@pytest.mark.asyncio
async def test_import_similarity_threshold_respected(settings) -> None:
    """find_similar is called with settings.skill_similarity_threshold."""
    raw = _valid_archive()
    client = MagicMock()
    settings = settings.model_copy(
        update={"openai_api_key": "sk-test", "skill_similarity_threshold": 0.9}
    )
    skill = _skill_response()

    with (
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_import_hash",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_name",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.embedding_service.embed_text",
            AsyncMock(return_value=[0.2] * 8),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.find_similar",
            AsyncMock(return_value=[]),
        ) as find_similar,
        patch(
            "app.services.skill_archive_service.skill_repo.upsert_imported",
            AsyncMock(return_value=(skill, False)),
        ),
        patch(
            "app.services.skill_archive_service.skill_embedding_service.maybe_embed_skill",
            AsyncMock(),
        ),
    ):
        await skill_archive_service.import_skill_archive(
            AsyncMock(),
            archive_bytes=raw,
            original_filename="demo-skill.zip",
            settings=settings,
            openai_client=client,
            overwrite=False,
        )

    assert find_similar.await_args.kwargs["threshold"] == 0.9


@pytest.mark.asyncio
async def test_import_name_override_skips_duplicate_check(settings) -> None:
    """Creating as new with name_override bypasses similarity conflict."""
    raw = _valid_archive()
    skill = _skill_response(name="demo-skill-v2")
    client = MagicMock()
    settings = settings.model_copy(update={"openai_api_key": "sk-test"})

    with (
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_import_hash",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.get_by_name",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.skill_archive_service.skill_repo.find_similar",
            AsyncMock(),
        ) as find_similar,
        patch(
            "app.services.skill_archive_service.skill_repo.upsert_imported",
            AsyncMock(return_value=(skill, False)),
        ) as upsert,
        patch(
            "app.services.skill_archive_service.skill_embedding_service.maybe_embed_skill",
            AsyncMock(),
        ),
    ):
        result = await skill_archive_service.import_skill_archive(
            AsyncMock(),
            archive_bytes=raw,
            original_filename="demo-skill.zip",
            settings=settings,
            openai_client=client,
            overwrite=False,
            name_override="demo-skill-v2",
        )

    assert result.name == "demo-skill-v2"
    find_similar.assert_not_awaited()
    assert upsert.await_args.kwargs["name"] == "demo-skill-v2"
