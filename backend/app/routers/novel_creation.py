"""REST API for API-free novel creation workflow."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.response import ApiResponse
from ..database.session import get_db
from ..database.models import NovelCreationSession, NovelCreationStageRun
from ..database.session import SessionLocal
from ..services.novel_creation_workspace import (
    STAGE_ORDER,
    create_run,
    get_presets,
    patch_session,
    serialize_run,
    serialize_session,
)
from ..services.workspace.tools.novel_creation import (
    apply_novel_blueprint,
    draft_novel_blueprint,
    review_novel_blueprint,
    start_novel_creation_session,
)
from ..services.workspace.tools.novel_creation_v2 import generate_novel_creation_stage, submit_novel_creation_stage

router = APIRouter(tags=["novel-creation"])


class NovelCreationStartRequest(BaseModel):
    mode: str = "template"
    user_brief: str = ""
    target_audience: str = ""
    genre: str = ""
    platform: str = ""
    preset_id: str = "free"
    theme_id: str = ""
    target_words: int = Field(600000, ge=10000, le=10000000)
    target_chapters: int = Field(240, ge=1, le=5000)
    world_tone: str = ""
    story_structure: str = ""
    pacing: str = ""
    writing_style: str = ""
    special_requirements: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    author_overrides: dict[str, Any] = Field(default_factory=dict)


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
    depth: Literal["concept", "full"] = "full"


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


class NovelCreationSessionPatchRequest(BaseModel):
    form: dict[str, Any] | None = None
    selected_concept_id: str | None = None
    quick_mode: bool | None = None


class NovelCreationStageRunRequest(BaseModel):
    stage: str
    model: str | None = None
    use_model: bool = True
    auto_confirm: bool = False
    operation: str = "generate"
    session_patch: dict[str, Any] | None = None


class NovelCreationStageConfirmRequest(BaseModel):
    data: dict[str, Any] | None = None
    confirm: bool = True
    source: str = "author"


@router.get("/novel-creation/presets")
async def novel_creation_presets():
    return ApiResponse.success(data=get_presets())


@router.get("/novel-creation/sessions")
async def list_creation_sessions(include_completed: bool = False, db: Session = Depends(get_db)):
    query = db.query(NovelCreationSession)
    if not include_completed:
        query = query.filter(NovelCreationSession.status.in_(["drafting", "reviewing", "failed"]))
    sessions = query.order_by(NovelCreationSession.updated_at.desc(), NovelCreationSession.created_at.desc()).limit(30).all()
    return ApiResponse.success(data={"sessions": [serialize_session(item, include_runs=False) for item in sessions]})


@router.get("/novel-creation/sessions/{session_id}")
async def get_creation_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    return ApiResponse.success(data=serialize_session(session))


@router.patch("/novel-creation/sessions/{session_id}")
async def update_creation_session(session_id: str, payload: NovelCreationSessionPatchRequest, db: Session = Depends(get_db)):
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    try:
        patch_session(session, payload.model_dump(exclude_none=True))
        db.commit()
        return ApiResponse.success(data=serialize_session(session), message="立项草稿已保存")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/novel-creation/sessions/{session_id}")
async def delete_creation_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
    if not session:
        return ApiResponse.success(data={"deleted": False})
    if session.created_project_id:
        raise HTTPException(status_code=409, detail="该立项已创建正式作品，不能删除会话记录")
    db.delete(session)
    db.commit()
    return ApiResponse.success(data={"deleted": True})


async def _run_creation_stage(run_id: str, session_id: str, request: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        await generate_novel_creation_stage(db, "", {**request, "session_id": session_id, "_run_id": run_id})
    finally:
        db.close()


@router.post("/novel-creation/sessions/{session_id}/runs")
async def start_creation_stage_run(session_id: str, payload: NovelCreationStageRunRequest, db: Session = Depends(get_db)):
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    if payload.stage not in {*STAGE_ORDER, "all"}:
        raise HTTPException(status_code=400, detail="未知立项阶段")
    request = payload.model_dump()
    run = create_run(db, session, payload.stage, request)
    db.commit()
    run_id = run.id
    asyncio.create_task(_run_creation_stage(run_id, session_id, request))
    return ApiResponse.success(data={"run": serialize_run(run), "stream_url": f"/api/novel-creation/runs/{run_id}/stream"}, message="阶段任务已创建")


@router.get("/novel-creation/runs/{run_id}")
async def get_creation_stage_run(run_id: str, db: Session = Depends(get_db)):
    run = db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="阶段任务不存在")
    return ApiResponse.success(data=serialize_run(run))


@router.get("/novel-creation/runs/{run_id}/stream")
async def stream_creation_stage_run(run_id: str):
    async def events():
        sent = 0
        idle = 0
        while idle < 900:
            db = SessionLocal()
            try:
                run = db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == run_id).first()
                if not run:
                    yield "event: error\ndata: " + json.dumps({"message": "阶段任务不存在"}, ensure_ascii=False) + "\n\n"
                    return
                rows = list(run.events or [])
                for event in rows[sent:]:
                    payload = {
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "status": event.status,
                        "message": event.message,
                        "payload": event.payload_json,
                    }
                    yield f"event: {event.event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                sent = len(rows)
                if run.status in {"completed", "failed", "cancelled"}:
                    yield "event: done\ndata: " + json.dumps(serialize_run(run), ensure_ascii=False) + "\n\n"
                    return
            finally:
                db.close()
            idle += 1
            await asyncio.sleep(0.5)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/novel-creation/sessions/{session_id}/stages/{stage}/confirm")
async def confirm_creation_stage(session_id: str, stage: str, payload: NovelCreationStageConfirmRequest, db: Session = Depends(get_db)):
    result = await submit_novel_creation_stage(db, "", {
        "session_id": session_id,
        "stage": stage,
        "data": payload.data,
        "confirm": payload.confirm,
        "source": payload.source,
    })
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
