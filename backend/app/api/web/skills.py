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
    SkillNameConflictError,
    SkillNotFoundError,
    SkillPackageTooLargeError,
    SkillPackagingError,
    SkillPathTraversalError,
)
from app.core.rate_limit import limiter
from app.models.schemas.skill import (
    ImportSkillResultSchema,
    SkillCreateSchema,
    SkillFileMetaSchema,
    SkillResponseSchema,
    SkillUpdateSchema,
)
from app.repositories import skill_files_repo, skill_repo
from app.services import skill_archive_service, skill_embedding_service

router = APIRouter(prefix="/api/skills", tags=["skills"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]

MAX_UPLOAD_BYTES = skill_archive_service.MAX_ARCHIVE_BYTES


@router.get(
    "",
    response_model=list[SkillResponseSchema],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("120/minute")
async def list_skills(
    request: Request,
    response: Response,
    session: DbSession,
    _user: CurrentUser,
) -> list[SkillResponseSchema]:
    _ = request, response
    return await skill_repo.list_all(session)


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
    file: UploadFile = File(...),  # noqa: B008
    overwrite: bool = Form(default=False),
    category: str | None = Form(default="billing"),
) -> ImportSkillResultSchema:
    """Import a Claude Agent Skill archive (.zip or .skill)."""
    _ = request, response
    filename = file.filename or "skill.zip"
    lower = filename.lower()
    if not (lower.endswith(".zip") or lower.endswith(".skill")):
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
            category=category,
        )
    except SkillAlreadyImportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "skill_id": str(exc.skill_id)},
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
@limiter.limit("120/minute")
async def list_skill_files(
    skill_id: uuid.UUID,
    request: Request,
    response: Response,
    session: DbSession,
    _user: CurrentUser,
) -> list[SkillFileMetaSchema]:
    _ = request, response
    skill = await skill_repo.get_by_id(session, skill_id)
    if skill is None:
        raise SkillNotFoundError(f"Skill not found: {skill_id}")
    return await skill_files_repo.list_by_skill(session, skill_id)


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
    skill = await skill_repo.get_by_id(session, skill_id)
    if skill is None:
        raise SkillNotFoundError(f"Skill not found: {skill_id}")
    path = unquote(relative_path)
    row = await skill_files_repo.get_by_path(
        session,
        skill_id=skill_id,
        relative_path=path,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}",
        )
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
    # CurrentAdmin/CurrentUser already autobegins this session — do not begin again.
    try:
        created = await skill_repo.create(session, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SkillBudgetExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SkillNameConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await skill_embedding_service.maybe_embed_skill(
        session,
        skill_id=created.id,
        name=created.name,
        description=created.description,
        body=created.content,
        settings=settings,
        openai_client=openai_client,
    )
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
        updated = await skill_repo.update_skill(session, skill_id, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SkillBudgetExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SkillNameConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if updated is None:
        raise SkillNotFoundError(f"Skill not found: {skill_id}")
    await skill_embedding_service.maybe_embed_skill(
        session,
        skill_id=updated.id,
        name=updated.name,
        description=updated.description,
        body=updated.content,
        settings=settings,
        openai_client=openai_client,
    )
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
    deleted = await skill_repo.delete_skill(session, skill_id)
    if not deleted:
        raise SkillNotFoundError(f"Skill not found: {skill_id}")
    await session.commit()
