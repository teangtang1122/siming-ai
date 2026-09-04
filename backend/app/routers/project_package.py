"""Authoritative Siming project-package import and export routes."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.gateway.infrastructure.service import GatewayService
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ..modules.story.interfaces.dependencies import get_story_command
from ..services.project_package_service import (
    ERROR_CONFLICT,
    ERROR_INVALID,
    ERROR_LIMIT,
    MAX_COMPRESSED_BYTES,
    PACKAGE_EXTENSION,
    PACKAGE_MEDIA_TYPE,
    ProjectPackageError,
    ProjectPackageExporter,
    ProjectPackageImporter,
    ProjectPackageValidator,
)

router = APIRouter(tags=["project-package"])
_UPLOAD_CHUNK_BYTES = 1024 * 1024
_ACTIVE_IMPORTS: dict[str, tuple[str, str, str | None]] = {}
_ACTIVE_IMPORTS_LOCK = threading.Lock()


def _paired_android(request: Request) -> bool:
    return bool(
        getattr(request.state, "gateway_device_id", None)
        and getattr(request.state, "gateway_device_platform", None) == "android"
    )


def _claim_active_import(key: str, package_sha256: str, title: str | None) -> str:
    token = str(uuid.uuid4())
    with _ACTIVE_IMPORTS_LOCK:
        active = _ACTIVE_IMPORTS.get(key)
        if active is not None:
            _, active_sha256, active_title = active
            if active_sha256 != package_sha256 or active_title != title:
                raise ProjectPackageError(
                    ERROR_CONFLICT,
                    "该 Idempotency-Key 正在用于不同的项目包或标题",
                    409,
                )
            raise ProjectPackageError(
                ERROR_CONFLICT,
                "相同项目包导入请求仍在处理中，请稍后重试",
                409,
            )
        _ACTIVE_IMPORTS[key] = (token, package_sha256, title)
    return token


def _release_active_import(key: str, token: str) -> None:
    with _ACTIVE_IMPORTS_LOCK:
        active = _ACTIVE_IMPORTS.get(key)
        if active is not None and active[0] == token:
            _ACTIVE_IMPORTS.pop(key, None)


async def _stage_upload(file: UploadFile) -> tuple[Path, Path, str]:
    if not (file.filename or "").lower().endswith(PACKAGE_EXTENSION):
        raise ProjectPackageError(
            ERROR_INVALID,
            "这里只接受 .siming-project；TXT/Markdown/DOCX 请使用“导入外部小说”",
            415,
        )
    temporary_root = Path(tempfile.mkdtemp(prefix="siming-project-package-upload-"))
    upload_path = temporary_root / f"upload{PACKAGE_EXTENSION}"
    digest = hashlib.sha256()
    written = 0
    try:
        with upload_path.open("wb") as destination:
            while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_COMPRESSED_BYTES:
                    raise ProjectPackageError(ERROR_LIMIT, "项目包超过 512MiB 上限", 413)
                digest.update(chunk)
                destination.write(chunk)
        if written == 0:
            raise ProjectPackageError(ERROR_INVALID, "上传的司命项目包为空")
        return upload_path, temporary_root, digest.hexdigest()
    except Exception:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise
    finally:
        await file.close()


@router.post("/projects/{project_id}/project-package/export")
def export_project_package(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    profile: Annotated[Literal["full", "structure"], Query()] = "full",
):
    """Stream a strict, versioned author-data package from a temporary file."""

    exported = ProjectPackageExporter(db, project_id, profile).build()
    return FileResponse(
        exported.path,
        media_type=PACKAGE_MEDIA_TYPE,
        filename=exported.filename,
        background=BackgroundTask(exported.cleanup),
    )


@router.post("/projects/project-package/import")
async def import_project_package(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    new_title: Annotated[str | None, Form()] = None,
):
    """Validate the complete package before creating a new project."""

    try:
        request_key = uuid.UUID(idempotency_key)
    except (ValueError, TypeError) as exc:
        raise ProjectPackageError(
            ERROR_INVALID,
            "Idempotency-Key 必须是 UUID",
        ) from exc
    title = (new_title or "").strip() or None
    if title is not None and len(title) > 200:
        raise ProjectPackageError(ERROR_INVALID, "新作品标题不能超过 200 个字符")

    upload_path, upload_root, package_sha256 = await _stage_upload(file)
    try:
        lease_token = _claim_active_import(str(request_key), package_sha256, title)
    except Exception:
        shutil.rmtree(upload_root, ignore_errors=True)
        raise
    validated = None
    importer = None
    committed = False
    try:
        validated = ProjectPackageValidator(upload_path, package_sha256).validate()
        importer = ProjectPackageImporter(
            db,
            validated,
            idempotency_key=request_key,
            new_title=title,
        )
        outcome = importer.restore()
        if not outcome.replayed:
            command.queue(
                ContentSyncIntent(
                    project_id=outcome.result["project_id"],
                    target=ContentSyncTarget.PROJECT,
                    source="project_package_import",
                )
            )
        if _paired_android(request) and not outcome.replayed:
            # This service commits the canonical mobile replica together with
            # the still-open project import transaction.
            GatewayService(db).enable_project(outcome.result["project_id"])
        command.finish()
        committed = True
        if outcome.replayed:
            message = (
                "项目包导入成功：已复用此前导入的作品"
                f"「{outcome.result['project_title']}」"
            )
        else:
            message = f"项目包导入成功：已创建作品「{outcome.result['project_title']}」"
        return ApiResponse.success(data=outcome.result, message=message)
    except Exception:
        if not committed:
            command.rollback()
            if importer is not None:
                importer.cleanup_after_failure()
        raise
    finally:
        if validated is not None:
            validated.cleanup()
        _release_active_import(str(request_key), lease_token)
        shutil.rmtree(upload_root, ignore_errors=True)


__all__ = ["router"]
