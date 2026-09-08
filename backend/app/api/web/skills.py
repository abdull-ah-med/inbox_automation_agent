"""Skills CRUD API — editable standing instructions for draft generation."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import unquote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import Response as RawResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import OpenAIClientDep, get_db
from app.core.dependencies_auth import CurrentAdmin, CurrentUser
from app.core.exceptions import (
    SkillAlreadyImportedError,
    SkillArchiveError,
    SkillBudgetExceededError,
    SkillDuplicateCandidatesError,
    SkillNameConflictError,
    SkillNotFoundError,
    SkillPackageTooLargeError,
    SkillPackagingError,
    SkillPathTraversalError,
)
from app.core.rate_limit import api_default_limit_value, limiter
from app.models.schemas.skill import (
    ImportSkillResultSchema,
    SkillCreateSchema,
    SkillFileMetaSchema,
    SkillResponseSchema,
    SkillUpdateSchema,
)
from app.services import skill_archive_service, skill_service

router = APIRouter(prefix="/api/skills", tags=["skills"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]

MAX_UPLOAD_BYTES = skill_archive_service.MAX_ARCHIVE_BYTES


def _skill_name_from_already_imported(message: str) -> str:
    """Best-effort name from ``Skill archive already imported as '…'``."""
    marker = " as '"
    if marker not in message or not message.endswith("'"):
        return "existing skill"
    return message.rsplit(marker, 1)[-1][:-1] or "existing skill"


@router.get(
    "",
    response_model=list[SkillResponseSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit(api_default_limit_value)
async def list_skills(
    request: Request,
    response: Response,
    session: DbSession,
    _user: CurrentUser,
) -> list[SkillResponseSchema]:
    _ = request, response
    return await skill_service.list_skills(session)


@router.post(
    "/import",
    response_model=ImportSkillResultSchema,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def import_skill(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    _admin: CurrentAdmin,
    file: Annotated[UploadFile, File()],
    overwrite: Annotated[bool, Form()] = False,
    overwrite_skill_id: Annotated[uuid.UUID | None, Form()] = None,
    name_override: Annotated[str | None, Form()] = None,
    category: Annotated[str | None, Form()] = "billing",
) -> ImportSkillResultSchema:
    """Import a Claude Agent Skill archive (.zip or .skill)."""
    _ = request, response
    filename = file.filename or "skill.zip"
    lower = filename.lower()
    if not (lower.endswith((".zip", ".skill"))):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Upload a .zip or .skill archive",
        )

    archive_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(archive_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Archive exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )
    if not archive_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty",
        )

    try:
        result = await skill_archive_service.import_skill_archive(
            session,
            archive_bytes=archive_bytes,
            original_filename=filename,
            settings=settings,
            openai_client=openai_client,
            overwrite=overwrite,
            overwrite_skill_id=overwrite_skill_id,
            name_override=name_override,
            category=category,
        )
    except SkillAlreadyImportedError as exc:
        # Same zip hash — surface via the overwrite / create-as-new dialog.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_candidates",
                "candidates": [
                    {
                        "id": str(exc.skill_id),
                        "name": _skill_name_from_already_imported(str(exc)),
                        "similarity": 1.0,
                    }
                ],
            },
        ) from exc
    except SkillDuplicateCandidatesError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_candidates",
                "candidates": [
                    {
                        "id": str(c["id"]),
                        "name": c["name"],
                        "similarity": c["similarity"],
                    }
                    for c in exc.candidates
                ],
            },
        ) from exc
    except SkillNameConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SkillPackageTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SkillPathTraversalError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SkillPackagingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SkillArchiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SkillBudgetExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await session.commit()
    return result


@router.get(
    "/{skill_id}/files",
    response_model=list[SkillFileMetaSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit(api_default_limit_value)
async def list_skill_files(
    skill_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    _user: CurrentUser,
) -> list[SkillFileMetaSchema]:
    _ = request, response
    return await skill_service.list_skill_files(session, skill_id)


@router.get(
    "/{skill_id}/files/{relative_path:path}",
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def get_skill_file(
    skill_id: uuid.UUID,
    relative_path: str,
    request: Request,
    response: Response,
    session: DbSession,
    _admin: CurrentAdmin,
) -> RawResponse:
    _ = request, response
    path = unquote(relative_path)
    try:
        row = await skill_service.get_skill_file(session, skill_id, path)
    except SkillNotFoundError as exc:
        detail = str(exc)
        if detail.startswith("File not found:"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=detail,
            ) from exc
        raise
    return RawResponse(
        content=row.content,
        media_type=row.mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{path.split("/")[-1]}"',
        },
    )


@router.post(
    "",
    response_model=SkillResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("60/minute")
async def create_skill(
    body: SkillCreateSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    _admin: CurrentAdmin,
) -> SkillResponseSchema:
    _ = request, response
    try:
        created = await skill_service.create_skill(
            session,
            body,
            settings=settings,
            openai_client=openai_client,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SkillBudgetExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SkillNameConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return created


@router.put(
    "/{skill_id}",
    response_model=SkillResponseSchema,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def update_skill(
    skill_id: uuid.UUID,
    body: SkillUpdateSchema,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    openai_client: OpenAIClientDep,
    _admin: CurrentAdmin,
) -> SkillResponseSchema:
    _ = request, response
    try:
        updated = await skill_service.update_skill(
            session,
            skill_id,
            body,
            settings=settings,
            openai_client=openai_client,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SkillBudgetExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SkillNameConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return updated


@router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit("60/minute")
async def delete_skill(
    skill_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    _admin: CurrentAdmin,
) -> None:
    _ = request, response
    await skill_service.delete_skill(session, skill_id)
    await session.commit()
