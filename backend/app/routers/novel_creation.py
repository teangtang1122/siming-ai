"""REST API for API-free novel creation workflow."""
from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.response import ApiResponse
from ..database.session import get_db
from ..services.workspace.tools.novel_creation import (
    apply_novel_blueprint,
    draft_novel_blueprint,
    review_novel_blueprint,
    start_novel_creation_session,
)

router = APIRouter(tags=["novel-creation"])


class NovelCreationStartRequest(BaseModel):
    mode: str = "template"
    user_brief: str = ""
    target_audience: str = ""
    genre: str = ""
    platform: str = ""


class NovelCreationDraftRequest(BaseModel):
    session_id: str
    execution_mode: Literal["template", "hybrid", "external_agent", "internal_llm"] = "hybrid"
    model: str | None = None
    user_brief: str = ""
    feedback: str = ""
    revision_mode: Literal["initial", "refine", "regenerate"] = "initial"
    enhance_with_llm: bool = False
    skip_questions: bool = False
    answers: dict[str, str] | None = None
    qa_history: list[dict[str, str]] | None = None


class NovelCreationReviewRequest(BaseModel):
    session_id: str
    execution_mode: Literal["template", "hybrid", "external_agent", "internal_llm"] = "hybrid"
    blueprint: Any | None = None


class NovelCreationApplyRequest(BaseModel):
    session_id: str
    blueprint_index: int = Field(0, ge=0)
    mode: Literal["manual", "auto"] = "auto"
    blueprint: Any | None = None


def _tool_response(result: dict[str, Any]) -> ApiResponse:
    status = result.get("status")
    detail = result.get("detail") or status or "success"
    if status not in ("ok", "need_clarification", "need_model"):
        raise HTTPException(status_code=400, detail=detail)
    return ApiResponse.success(data=result.get("data"), message=detail)


@router.post("/novel-creation/start")
async def start_creation(payload: NovelCreationStartRequest, db: Session = Depends(get_db)):
    result = await start_novel_creation_session(db, "", payload.model_dump())
    return _tool_response(result)


@router.post("/novel-creation/draft")
async def draft_blueprints(payload: NovelCreationDraftRequest, db: Session = Depends(get_db)):
    result = await draft_novel_blueprint(db, "", payload.model_dump())
    return _tool_response(result)


@router.post("/novel-creation/review")
async def review_blueprint(payload: NovelCreationReviewRequest, db: Session = Depends(get_db)):
    result = await review_novel_blueprint(db, "", payload.model_dump())
    return _tool_response(result)


@router.post("/novel-creation/apply")
async def apply_blueprint(payload: NovelCreationApplyRequest, db: Session = Depends(get_db)):
    result = await apply_novel_blueprint(db, "", payload.model_dump())
    return _tool_response(result)


class RefreshQuestionRequest(BaseModel):
    session_id: str
    question: str
    existing_options: list[str] = []
    user_brief: str = ""
    model: str | None = None


@router.post("/novel-creation/refresh-question")
async def refresh_question(payload: RefreshQuestionRequest, db: Session = Depends(get_db)):
    from app.services.workspace.tools.novel_creation import refresh_question_options
    result = await refresh_question_options(
        db=db,
        session_id=payload.session_id,
        question=payload.question,
        existing_options=payload.existing_options,
        user_brief=payload.user_brief,
        model=payload.model,
    )
    return ApiResponse.success(data=result)


class SystemChatRequest(BaseModel):
    message: str
    model: str | None = None
    context: dict[str, Any] | None = None  # {blueprints, sessionId, brief, importedFiles, history}


@router.post("/novel-creation/system-chat")
async def system_chat(payload: SystemChatRequest):
    """General conversation endpoint for system assistant without project context."""
    from app.services.workspace.tools.novel_creation import system_chat_completion
    result = await system_chat_completion(
        message=payload.message,
        context=payload.context or {},
        model=payload.model,
    )
    return ApiResponse.success(data=result)


class SaveImportedFileRequest(BaseModel):
    filename: str
    content: str


@router.post("/novel-creation/save-imported-file")
async def save_imported_file(payload: SaveImportedFileRequest):
    """Save an imported file to the working directory for LLM CLI access."""
    from app.services.content_store import content_root
    import os

    root = content_root()
    imported_dir = root / ".imported"
    imported_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '-', payload.filename)
    safe_name = safe_name.strip(' .-')[:200]

    # Add timestamp to avoid conflicts
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_parts = safe_name.rsplit('.', 1)
    if len(name_parts) == 2:
        final_name = f"{name_parts[0]}_{timestamp}.{name_parts[1]}"
    else:
        final_name = f"{safe_name}_{timestamp}"

    file_path = imported_dir / final_name
    file_path.write_text(payload.content, encoding='utf-8')

    return ApiResponse.success(data={
        "path": str(file_path),
        "filename": final_name,
        "size": len(payload.content),
    })


@router.get("/novel-creation/imported-files")
async def list_imported_files():
    """List all imported files in the working directory."""
    from app.services.content_store import content_root
    from datetime import datetime

    root = content_root()
    imported_dir = root / ".imported"
    if not imported_dir.exists():
        return ApiResponse.success(data={"files": [], "directory": str(imported_dir)})

    files = []
    for f in sorted(imported_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file():
            files.append({
                "filename": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })

    return ApiResponse.success(data={"files": files, "directory": str(imported_dir)})


@router.get("/novel-creation/imported-files/{filename}")
async def read_imported_file(filename: str):
    """Read the content of a specific imported file."""
    from app.services.content_store import content_root

    root = content_root()
    imported_dir = root / ".imported"
    file_path = imported_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    # Security: prevent path traversal
    if not file_path.resolve().is_relative_to(imported_dir.resolve()):
        raise HTTPException(status_code=403, detail="访问被拒绝")

    content = file_path.read_text(encoding='utf-8')
    return ApiResponse.success(data={
        "filename": filename,
        "content": content,
        "size": len(content),
        "path": str(file_path),
    })
