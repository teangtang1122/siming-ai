"""Durable, checkpointed material imports for novel-creation sessions."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from sqlalchemy.orm import Session

from app.ai.local_cli_adapter import is_local_cli_provider
from app.architecture.uow import commit_session
from app.core.json_repair import parse_json_object
from app.database.session import SessionLocal
from app.modules.creation.infrastructure.models import (
    NovelCreationImportChunk,
    NovelCreationMaterialImport,
    NovelCreationSession,
)
from app.modules.model_runtime.application.execution import model_executor as LLMGateway
from app.modules.operations.infrastructure.models import OperationRun
from app.services.content_store import content_root
from app.services.character_role_types import normalize_character_role_type
from app.services.novel_creation_authoring import _validate_stage
from app.services.novel_creation_contract import OPENING_OUTLINE_CHAPTER_COUNT
from app.services.novel_creation_workspace import save_stage, serialize_creation_artifact
from app.services.operation_runtime import (
    activate_operation,
    ensure_operation,
    fail_operation,
    finish_operation,
    heartbeat_loop,
    record_operation_signal,
    unregister_operation_actions,
)

SUPPORTED_EXTENSIONS = {"txt", "md", "docx", "json"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
CHUNK_SIZE = 8_000
CHUNK_OVERLAP = 400
IMPORTABLE_ARTIFACTS = ("world_style", "characters", "locations", "macro_outline", "opening_outline")


def _now() -> datetime:
    return datetime.utcnow()


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_creation_material(filename: str, raw: bytes) -> tuple[str, str]:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("仅支持 txt、md、docx 和 json 文件")
    if not raw:
        raise ValueError("文件内容为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("文件超过 25MB 上限")
    if extension == "docx":
        document = DocxDocument(io.BytesIO(raw))
        text = "\n\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
    else:
        text = _decode_text(raw)
    if not text.strip():
        raise ValueError("文件没有可导入的文本内容")
    if extension == "json":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 文件格式无效：{exc.msg}") from exc
        text = json.dumps(parsed, ensure_ascii=False, indent=2)
    return text, extension


def split_creation_material(text: str) -> list[tuple[int, int, str]]:
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        if end < len(text):
            boundary = text.rfind("\n", start + CHUNK_SIZE // 2, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end]
        if chunk.strip():
            chunks.append((start, end, chunk))
        if end >= len(text):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks


def _provenance(import_run: NovelCreationMaterialImport, chunk_index: int, confidence: float) -> dict[str, Any]:
    return {
        "source_file_id": import_run.id,
        "source_chunk": chunk_index,
        "source_message_id": import_run.source_message_id,
        "import_run_id": import_run.id,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
    }


def _candidate(value: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    result["_source"] = deepcopy(provenance)
    return result


def _as_rows(value: Any, *, key_name: str = "name") -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [deepcopy(row) for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        rows: list[dict[str, Any]] = []
        for key, child in value.items():
            if isinstance(child, dict):
                row = deepcopy(child)
                row.setdefault(key_name, str(key))
                rows.append(row)
        return rows
    return []


def _extract_structured_json(text: str, provenance: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        parsed = parse_json_object(text)
        payload = parsed if isinstance(parsed, dict) else None
    if not isinstance(payload, dict):
        return {}
    root = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    artifacts = root.get("artifacts") if isinstance(root.get("artifacts"), dict) else root
    characters = _as_rows(artifacts.get("characters"))
    if len(characters) == 1 and isinstance(characters[0].get("characters"), list):
        characters = _as_rows(characters[0].get("characters"))
    locations = _as_rows(artifacts.get("locations"), key_name="title")
    factions = _as_rows(artifacts.get("factions"), key_name="title")
    worldbuilding = _as_rows(artifacts.get("worldbuilding"), key_name="title")
    volumes = _as_rows(artifacts.get("volumes") or artifacts.get("volume_outline"), key_name="title")
    chapters = _as_rows(artifacts.get("chapters") or artifacts.get("chapter_summaries"), key_name="title")
    return {
        "characters": [_candidate(row, provenance) for row in characters],
        "locations": [_candidate({**row, "entity_type": row.get("entity_type") or "location"}, provenance) for row in locations],
        "factions": [_candidate({**row, "entity_type": row.get("entity_type") or "faction"}, provenance) for row in factions],
        "worldbuilding": [_candidate(row, provenance) for row in worldbuilding],
        "volumes": [_candidate(row, provenance) for row in volumes],
        "chapters": [_candidate(row, provenance) for row in chapters],
    }


_VOLUME_HEADING = re.compile(r"(?m)^\s*(?:#{1,4}\s*)?(第[零一二三四五六七八九十百千万0-9]+卷[^\n]*)$")
_CHAPTER_HEADING = re.compile(r"(?m)^\s*(?:#{1,5}\s*)?(第[零一二三四五六七八九十百千万0-9]+章[^\n]*)$")
_SECTION_HEADING = re.compile(r"(?m)^\s*(?:#{1,5}\s*)?(人物|角色|人物设定|角色设定|地点|场景|势力|组织|世界观|世界设定)\s*[：:]?\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)([^：:\n]{1,40})[：:]\s*(.+)$")


def _heading_items(text: str, pattern: re.Pattern[str], provenance: dict[str, Any]) -> list[dict[str, Any]]:
    matches = list(pattern.finditer(text))
    rows: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        rows.append(_candidate({"title": match.group(1).strip(), "summary": body[:2000]}, provenance))
    return rows


def deterministic_extract_chunk(text: str, import_run: NovelCreationMaterialImport, chunk_index: int) -> dict[str, Any]:
    provenance = _provenance(import_run, chunk_index, 0.72)
    structured = _extract_structured_json(text, _provenance(import_run, chunk_index, 0.96))
    volumes = structured.get("volumes") or _heading_items(text, _VOLUME_HEADING, provenance)
    chapters = structured.get("chapters") or _heading_items(text, _CHAPTER_HEADING, provenance)
    characters = list(structured.get("characters") or [])
    locations = list(structured.get("locations") or [])
    factions = list(structured.get("factions") or [])
    worldbuilding = list(structured.get("worldbuilding") or [])
    sections = list(_SECTION_HEADING.finditer(text))
    for index, section in enumerate(sections):
        end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
        kind = section.group(1)
        for line in text[section.end():end].splitlines():
            match = _BULLET.match(line)
            if not match:
                continue
            title, description = match.groups()
            row = _candidate({"name": title.strip(), "title": title.strip(), "description": description.strip()}, provenance)
            if "人物" in kind or "角色" in kind:
                characters.append(row)
            elif "地点" in kind or "场景" in kind:
                row["entity_type"] = "location"
                locations.append(row)
            elif "势力" in kind or "组织" in kind:
                row["entity_type"] = "faction"
                factions.append(row)
            else:
                worldbuilding.append(row)
    return {
        "characters": characters,
        "locations": locations,
        "factions": factions,
        "worldbuilding": worldbuilding,
        "volumes": volumes,
        "chapters": chapters,
        "notes": [_candidate({"text": text[:2000]}, _provenance(import_run, chunk_index, 0.45))],
        "method": "deterministic",
    }


async def _model_extract_chunk(text: str, import_run: NovelCreationMaterialImport, chunk_index: int, model: str) -> dict[str, Any] | None:
    schema = {
        "characters": [{"name": "", "description": "", "goal": "", "role_type": "protagonist|supporting"}],
        "locations": [{"title": "", "description": "", "entity_type": "location"}],
        "factions": [{"title": "", "description": "", "entity_type": "faction"}],
        "worldbuilding": [{"title": "", "description": ""}],
        "volumes": [{"title": "", "summary": "", "start_chapter": 1, "end_chapter": 20}],
        "chapters": [{"title": "", "summary": "", "chapter_number": 1}],
    }
    result = await LLMGateway.chat_completion(
        messages=[
            {"role": "system", "content": "你只做资料提取，不续写、不补写。只返回 JSON；没有证据的数组保持为空。"},
            {"role": "user", "content": f"从以下小说资料分块提取候选结构。结构：{json.dumps(schema, ensure_ascii=False)}\n\n资料：\n{text}"},
        ],
        model=model,
        temperature=0.1,
        max_tokens=5000,
        extra_body=LLMGateway.local_cli_extra_body(
            model,
            cwd=str(content_root()),
            base={"moshu_task_type": "planning", "moshu_storage_target": "creation_import"},
        ) if is_local_cli_provider(LLMGateway.model_identity(model, {"moshu_task_type": "planning"})[0]) else None,
    )
    parsed = parse_json_object(_text(result.get("content")))
    if not isinstance(parsed, dict):
        return None
    provenance = _provenance(import_run, chunk_index, 0.88)
    extracted: dict[str, Any] = {"method": "model"}
    for key in ("characters", "locations", "factions", "worldbuilding", "volumes", "chapters"):
        extracted[key] = [_candidate(row, provenance) for row in _as_rows(parsed.get(key), key_name="title" if key != "characters" else "name")]
    return extracted


def _identity(kind: str, row: dict[str, Any]) -> str:
    if kind == "characters":
        return _text(row.get("name")).casefold()
    if kind == "chapters":
        return _text(row.get("chapter_number") or row.get("title")).casefold()
    return _text(row.get("title") or row.get("name")).casefold()


def merge_import_extractions(chunks: list[NovelCreationImportChunk]) -> dict[str, Any]:
    merged: dict[str, list[dict[str, Any]]] = {
        "characters": [], "locations": [], "factions": [], "worldbuilding": [], "volumes": [], "chapters": [], "notes": [],
    }
    seen: dict[str, set[str]] = {key: set() for key in merged}
    duplicates: list[dict[str, Any]] = []
    for chunk in chunks:
        extraction = chunk.extraction_json if isinstance(chunk.extraction_json, dict) else {}
        for kind in merged:
            rows = extraction.get(kind) if isinstance(extraction.get(kind), list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                identity = _identity(kind, row)
                if identity and identity in seen[kind]:
                    duplicates.append({"kind": kind, "identity": identity, "source_chunk": chunk.chunk_index})
                    continue
                if identity:
                    seen[kind].add(identity)
                merged[kind].append(deepcopy(row))
    merged["volumes"].sort(key=lambda row: int(row.get("start_chapter") or 10**9))
    merged["chapters"].sort(key=lambda row: int(row.get("chapter_number") or 10**9))
    return {"candidates": merged, "duplicates": duplicates}


def build_import_preview(import_run: NovelCreationMaterialImport, session: NovelCreationSession) -> dict[str, Any]:
    merged = merge_import_extractions(list(import_run.chunks or []))
    candidates = merged["candidates"]
    artifact_counts = {
        "characters": len(candidates["characters"]),
        "locations": len(candidates["locations"]) + len(candidates["factions"]),
        "world_style": len(candidates["worldbuilding"]) + len(candidates["notes"]),
        "macro_outline": len(candidates["volumes"]),
        "opening_outline": len(candidates["chapters"]),
    }
    conflicts: list[dict[str, Any]] = list(merged["duplicates"])
    for artifact, count in artifact_counts.items():
        if not count:
            continue
        current = serialize_creation_artifact(session, artifact)
        if current.get("data") is not None:
            conflicts.append({
                "kind": "existing_artifact",
                "artifact": artifact,
                "status": current.get("status"),
                "locked_paths": current.get("locked_paths") or [],
            })
    return {
        "source_file_id": import_run.id,
        "import_run_id": import_run.id,
        "filename": import_run.filename,
        "text_length": import_run.text_length,
        "chunk_count": import_run.chunk_count,
        "processed_chunks": import_run.processed_chunks,
        "artifact_counts": artifact_counts,
        "detected": {
            "characters": artifact_counts["characters"],
            "factions": len(candidates["factions"]),
            "locations": len(candidates["locations"]),
            "volumes": artifact_counts["macro_outline"],
            "chapter_summaries": artifact_counts["opening_outline"],
        },
        "candidates": candidates,
        "conflicts": conflicts,
        "available_artifacts": [key for key, count in artifact_counts.items() if count],
    }


def serialize_material_import(import_run: NovelCreationMaterialImport, *, include_preview: bool = True) -> dict[str, Any]:
    data = {
        "id": import_run.id,
        "source_file_id": import_run.id,
        "session_id": import_run.session_id,
        "operation_id": import_run.operation_id,
        "source_message_id": import_run.source_message_id,
        "filename": import_run.filename,
        "media_type": import_run.media_type,
        "file_sha256": import_run.file_sha256,
        "size_bytes": import_run.size_bytes,
        "status": import_run.status,
        "input_revision": import_run.input_revision,
        "text_length": import_run.text_length,
        "chunk_count": import_run.chunk_count,
        "processed_chunks": import_run.processed_chunks,
        "checkpoint": deepcopy(import_run.checkpoint_json),
        "selection": deepcopy(import_run.selection_json),
        "result": deepcopy(import_run.result_json),
        "error": import_run.error,
        "created_at": import_run.created_at.isoformat() if import_run.created_at else None,
        "updated_at": import_run.updated_at.isoformat() if import_run.updated_at else None,
        "completed_at": import_run.completed_at.isoformat() if import_run.completed_at else None,
    }
    if include_preview:
        data["preview"] = deepcopy(import_run.preview_json)
    return data


def create_material_import(
    db: Session,
    session: NovelCreationSession,
    *,
    filename: str,
    raw: bytes,
    model: str | None = None,
    source_message_id: str | None = None,
    media_type: str | None = None,
) -> tuple[NovelCreationMaterialImport, bool]:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("仅支持 txt、md、docx 和 json 文件")
    if not raw:
        raise ValueError("文件内容为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("文件超过 25MB 上限")
    digest = hashlib.sha256(raw).hexdigest()
    existing = db.query(NovelCreationMaterialImport).filter_by(session_id=session.id, file_sha256=digest).first()
    if existing:
        return existing, True
    import_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"siming-creation-import:{session.id}:{digest}"))
    safe_name = re.sub(r'[^\w.\-\u4e00-\u9fff]+', '-', filename, flags=re.UNICODE).strip(" .-")[:180]
    safe_name = safe_name or f"material.{extension}"
    import_dir = content_root() / ".creation-imports" / session.id / import_id
    import_dir.mkdir(parents=True, exist_ok=True)
    stored_path = import_dir / f"original-{safe_name}"
    stored_path.write_bytes(raw)
    model_source: str | None = model
    tool_mode = "deterministic_import"
    if model:
        try:
            selection = LLMGateway.select_model_for_task(task_type="planning", model_override=model)
            effective = selection.model or model
            provider, model_name = LLMGateway.model_identity(effective, {"moshu_task_type": "planning"})
            model_source = f"{provider}:{model_name}"
            tool_mode = "local_cli_stream" if is_local_cli_provider(provider) else "api_stream"
        except Exception:
            pass
    operation = ensure_operation(
        db,
        source_kind="novel_creation_import",
        source_id=import_id,
        title=f"导入立项资料 · {filename}",
        phase="reading_file",
        message="原始文件已保存，正在分块读取",
        model_source=model_source,
        tool_mode=tool_mode,
        resume_url=f"/gui?creationSession={session.id}&import={import_id}",
        can_pause=False,
        can_cancel=True,
        can_retry=True,
        progress_mode="steps",
        progress_current=0,
        input_revision=int(session.revision or 0),
        snapshot_hash=digest,
    )
    import_run = NovelCreationMaterialImport(
        id=import_id,
        session_id=session.id,
        operation_id=operation.id,
        source_message_id=source_message_id,
        filename=filename,
        stored_path=str(stored_path),
        media_type=media_type or extension,
        file_sha256=digest,
        size_bytes=len(raw),
        status="queued",
        input_revision=int(session.revision or 0),
        checkpoint_json={"phase": "queued", "next_chunk": 0},
    )
    db.add(import_run)
    db.flush()
    return import_run, False


async def run_material_import(import_id: str, model: str | None = None) -> None:
    heartbeat_task: asyncio.Task[Any] | None = None
    operation_id: str | None = None
    try:
        with SessionLocal() as db:
            import_run = db.get(NovelCreationMaterialImport, import_id)
            if not import_run:
                return
            operation_id = import_run.operation_id
            import_run.status = "running"
            import_run.error = None
            import_run.updated_at = _now()
            commit_session(db)
        if operation_id:
            heartbeat_task = asyncio.create_task(heartbeat_loop(operation_id))
        with activate_operation(operation_id):
            with SessionLocal() as db:
                import_run = db.get(NovelCreationMaterialImport, import_id)
                if not import_run:
                    return
                raw = Path(import_run.stored_path).read_bytes()
                text, extension = parse_creation_material(import_run.filename, raw)
                import_run.media_type = extension
                import_run.text_length = len(text)
                existing = {chunk.chunk_index for chunk in import_run.chunks}
                chunks = split_creation_material(text)
                import_run.chunk_count = len(chunks)
                for index, (start, end, chunk_text) in enumerate(chunks):
                    if index in existing:
                        continue
                    db.add(NovelCreationImportChunk(
                        import_run_id=import_run.id,
                        chunk_index=index,
                        char_start=start,
                        char_end=end,
                        content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                        text=chunk_text,
                        status="pending",
                    ))
                import_run.checkpoint_json = {"phase": "extracting", "next_chunk": import_run.processed_chunks}
                commit_session(db)
            for index in range(len(chunks)):
                with SessionLocal() as db:
                    import_run = db.get(NovelCreationMaterialImport, import_id)
                    chunk = db.query(NovelCreationImportChunk).filter_by(import_run_id=import_id, chunk_index=index).first()
                    if not import_run or not chunk or chunk.status == "completed":
                        continue
                    extraction = deterministic_extract_chunk(chunk.text, import_run, index)
                    if model:
                        try:
                            model_extraction = await _model_extract_chunk(chunk.text, import_run, index, model)
                            if model_extraction:
                                extraction = model_extraction
                        except Exception as exc:
                            extraction["model_warning"] = str(exc)[:1000]
                    chunk.extraction_json = extraction
                    chunk.status = "completed"
                    chunk.confidence = 88 if extraction.get("method") == "model" else 72
                    chunk.updated_at = _now()
                    import_run.processed_chunks = db.query(NovelCreationImportChunk).filter_by(import_run_id=import_id, status="completed").count()
                    import_run.processed_chunks = min(import_run.processed_chunks, import_run.chunk_count)
                    import_run.checkpoint_json = {"phase": "extracting", "next_chunk": index + 1}
                    import_run.updated_at = _now()
                    commit_session(db)
                    record_operation_signal(
                        operation_id,
                        "checkpoint",
                        {
                            "phase": "extracting",
                            "progress_current": import_run.processed_chunks,
                            "progress_total": import_run.chunk_count,
                            "progress_mode": "steps",
                            "chunk_index": index,
                        },
                        f"已整理 {import_run.processed_chunks}/{import_run.chunk_count} 个资料分块",
                    )
            with SessionLocal() as db:
                import_run = db.get(NovelCreationMaterialImport, import_id)
                session = db.get(NovelCreationSession, import_run.session_id) if import_run else None
                if not import_run or not session:
                    raise ValueError("立项会话不存在")
                import_run.processed_chunks = import_run.chunk_count
                import_run.preview_json = build_import_preview(import_run, session)
                import_run.checkpoint_json = {"phase": "preview_ready", "next_chunk": import_run.chunk_count}
                import_run.status = "waiting_user"
                import_run.updated_at = _now()
                if import_run.operation_id:
                    operation = db.get(OperationRun, import_run.operation_id)
                    if operation:
                        operation.can_cancel = False
                        operation.can_pause = False
                commit_session(db)
                preview = deepcopy(import_run.preview_json)
        finish_operation(
            operation_id,
            status="waiting_user",
            message="导入预览已生成，等待选择要写入的内容",
            next_action="检查冲突并选择要导入的立项数据",
            result={"import_id": import_id, "preview": preview},
        )
    except asyncio.CancelledError:
        with SessionLocal() as db:
            import_run = db.get(NovelCreationMaterialImport, import_id)
            if import_run:
                import_run.status = "cancelled"
                import_run.error = "用户取消了导入"
                import_run.completed_at = _now()
                commit_session(db)
        finish_operation(operation_id, status="cancelled", message="导入已取消，已完成的分块检查点仍然保留")
        raise
    except Exception as exc:
        with SessionLocal() as db:
            import_run = db.get(NovelCreationMaterialImport, import_id)
            if import_run:
                import_run.status = "failed"
                import_run.error = str(exc)[:4000]
                import_run.updated_at = _now()
                commit_session(db)
        fail_operation(operation_id, exc, next_action="可从已保存的分块检查点重试导入")
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        unregister_operation_actions(operation_id)


def _source_from(rows: list[dict[str, Any]], import_run: NovelCreationMaterialImport) -> dict[str, Any]:
    sources = [deepcopy(row.get("_source")) for row in rows if isinstance(row.get("_source"), dict)]
    return {
        "source_file_id": import_run.id,
        "source_message_id": import_run.source_message_id,
        "import_run_id": import_run.id,
        "source_chunks": sorted({int(source.get("source_chunk", 0)) for source in sources}),
        "confidence": round(sum(float(source.get("confidence", 0)) for source in sources) / max(1, len(sources)), 2),
    }


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in row.items() if key != "_source"}


def _artifact_payload(artifact: str, candidates: dict[str, Any], import_run: NovelCreationMaterialImport) -> dict[str, Any] | None:
    if artifact == "characters":
        rows = [row for row in candidates.get("characters", []) if isinstance(row, dict) and _text(row.get("name") or row.get("title"))]
        if not rows:
            return None
        characters = []
        for index, row in enumerate(rows):
            item = _clean_row(row)
            item["name"] = _text(item.get("name") or item.get("title"))
            raw_role_type = item.get("role_type")
            item["role_type"] = normalize_character_role_type(
                raw_role_type,
                default="protagonist" if index == 0 else "supporting",
            )
            item["goal"] = _text(item.get("goal") or item.get("current_goal"), "待作者补充")
            characters.append(item)
        return {"characters": characters, "relationships": [], "_import_provenance": _source_from(rows, import_run)}
    if artifact == "locations":
        rows = [row for row in [*candidates.get("locations", []), *candidates.get("factions", [])] if isinstance(row, dict) and _text(row.get("title") or row.get("name"))]
        if not rows:
            return None
        entries = []
        for row in rows:
            item = _clean_row(row)
            item["title"] = _text(item.get("title") or item.get("name"))
            item["description"] = _text(item.get("description"), "待作者补充")
            item["entry_type"] = _text(item.get("entry_type") or item.get("entity_type"), "location")
            entries.append(item)
        return {"entries": entries, "relations": [], "_import_provenance": _source_from(rows, import_run)}
    if artifact == "world_style":
        rows = [row for row in [*candidates.get("worldbuilding", []), *candidates.get("notes", [])] if isinstance(row, dict)]
        if not rows:
            return None
        readable = "\n".join(_text(row.get("description") or row.get("text") or row.get("title")) for row in rows if _text(row.get("description") or row.get("text") or row.get("title")))
        worldbuilding = [
            {"title": _text(row.get("title"), f"导入设定 {index + 1}"), "description": _text(row.get("description") or row.get("text"), "待作者补充")}
            for index, row in enumerate(rows[:100])
        ]
        return {
            "writing_style": "按导入资料保留原有叙述风格",
            "world_tone": readable[:1000] or "待作者补充",
            "story_structure": "按导入资料中的既有结构整理",
            "pacing": "待作者补充",
            "style_rules": [], "forbidden_patterns": [], "worldbuilding": worldbuilding,
            "_import_provenance": _source_from(rows, import_run),
        }
    if artifact == "macro_outline":
        rows = [row for row in candidates.get("volumes", []) if isinstance(row, dict) and _text(row.get("title"))]
        if not rows:
            return None
        volumes = []
        previous_end = 0
        for index, row in enumerate(rows):
            item = _clean_row(row)
            start = int(item.get("start_chapter") or previous_end + 1)
            end = max(start, int(item.get("end_chapter") or start + 19))
            item.update({"title": _text(item.get("title"), f"第{index + 1}卷"), "summary": _text(item.get("summary") or item.get("description"), "待作者补充"), "start_chapter": start, "end_chapter": end})
            previous_end = end
            volumes.append(item)
        summary = "；".join(_text(row.get("summary")) for row in volumes if _text(row.get("summary")))[:3000]
        return {"story_overview": summary or "按导入资料整理", "core_conflict": "待作者补充", "ending_direction": "待作者补充", "target_chapters": previous_end, "volumes": volumes, "stage_plan": [], "_import_provenance": _source_from(rows, import_run)}
    if artifact == "opening_outline":
        rows = [row for row in candidates.get("chapters", []) if isinstance(row, dict) and _text(row.get("title"))][:OPENING_OUTLINE_CHAPTER_COUNT]
        if len(rows) < OPENING_OUTLINE_CHAPTER_COUNT:
            return None
        chapters: list[dict[str, Any]] = []
        sections: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            client_id = f"import-chapter-{index + 1}"
            title = _text(row.get("title"), f"第{index + 1}章")
            summary = _text(row.get("summary") or row.get("description"), "待作者补充")
            chapters.append({"client_id": client_id, "chapter_number": index + 1, "title": title, "summary": summary})
            for scene_number in (1, 2):
                sections.append({
                    "client_id": f"{client_id}-scene-{scene_number}", "parent_client_id": client_id,
                    "title": f"{title}·场景{scene_number}", "summary": summary,
                    "metadata": {"scene_number": scene_number, "purpose": "按导入摘要拆分，待作者校验", "location": "待作者补充", "timeline": "待作者补充", "pov_character": "待作者补充", "characters": [], "entry_state": "待作者补充", "exit_state": "待作者补充", "emotional_residue": "待作者补充", "unresolved_actions": []},
                })
        return {"opening_chapter_count": OPENING_OUTLINE_CHAPTER_COUNT, "chapters": chapters, "sections": sections, "_import_provenance": _source_from(rows, import_run)}
    return None


def _merge_payload(artifact: str, current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(current)
    list_fields = {
        "characters": ("characters", "name"),
        "locations": ("entries", "title"),
        "world_style": ("worldbuilding", "title"),
        "macro_outline": ("volumes", "title"),
        "opening_outline": ("chapters", "client_id"),
    }
    field, identity_key = list_fields[artifact]
    existing = merged.get(field) if isinstance(merged.get(field), list) else []
    identities = {_text(row.get(identity_key)).casefold() for row in existing if isinstance(row, dict)}
    additions = [row for row in incoming.get(field, []) if isinstance(row, dict) and _text(row.get(identity_key)).casefold() not in identities]
    merged[field] = [*existing, *additions]
    if artifact == "locations":
        merged.setdefault("relations", [])
    if artifact == "characters":
        merged.setdefault("relationships", [])
    if artifact == "opening_outline":
        known_parents = {_text(row.get("client_id")) for row in additions}
        merged["sections"] = [*(merged.get("sections") or []), *[row for row in incoming.get("sections", []) if _text(row.get("parent_client_id")) in known_parents]]
    merged["_import_provenance"] = deepcopy(incoming.get("_import_provenance"))
    return merged


def apply_material_import(
    db: Session,
    import_run: NovelCreationMaterialImport,
    *,
    selected_artifacts: list[str],
    strategy: str,
    expected_revision: int,
) -> dict[str, Any]:
    if import_run.status != "waiting_user" or not isinstance(import_run.preview_json, dict):
        raise ValueError("导入预览尚未就绪")
    session = db.get(NovelCreationSession, import_run.session_id)
    if not session:
        raise ValueError("立项会话不存在")
    if int(session.revision or 0) != int(expected_revision):
        raise RuntimeError("revision_conflict")
    if strategy not in {"merge", "overwrite_unconfirmed", "skip_conflicts"}:
        raise ValueError("未知的导入策略")
    selected = [artifact for artifact in selected_artifacts if artifact in IMPORTABLE_ARTIFACTS]
    if not selected:
        raise ValueError("请至少选择一类可导入数据")
    candidates = import_run.preview_json.get("candidates") or {}
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for artifact in selected:
        incoming = _artifact_payload(artifact, candidates, import_run)
        if not incoming:
            skipped.append({"artifact": artifact, "reason": "no_candidates"})
            continue
        current = serialize_creation_artifact(session, artifact)
        if current.get("locked_paths") and current.get("data") is not None:
            skipped.append({"artifact": artifact, "reason": "locked_fields"})
            continue
        if strategy == "skip_conflicts" and current.get("data") is not None:
            skipped.append({"artifact": artifact, "reason": "existing_artifact"})
            continue
        if strategy == "overwrite_unconfirmed" and current.get("status") == "confirmed":
            skipped.append({"artifact": artifact, "reason": "confirmed_artifact"})
            continue
        if strategy == "merge" and artifact == "opening_outline" and current.get("data") is not None:
            skipped.append({"artifact": artifact, "reason": "non_mergeable_fixed_chapter_window"})
            continue
        payload = incoming
        if strategy == "merge" and isinstance(current.get("data"), dict):
            payload = _merge_payload(artifact, current["data"], incoming)
        _validate_stage(artifact, payload)
        save_stage(session, artifact, payload, confirm=False, source=f"import:{import_run.id}")
        applied.append({"artifact": artifact, "count": int((import_run.preview_json.get("artifact_counts") or {}).get(artifact) or 0)})
    import_run.selection_json = {"artifacts": selected, "strategy": strategy, "expected_revision": expected_revision}
    import_run.result_json = {"applied": applied, "skipped": skipped, "revision": int(session.revision or 0)}
    import_run.status = "completed"
    import_run.completed_at = _now()
    import_run.updated_at = _now()
    finish_operation(import_run.operation_id, message="导入内容已写入立项数据", result=deepcopy(import_run.result_json), db=db)
    commit_session(db)
    return deepcopy(import_run.result_json)


def get_material_import_record(db: Session, import_id: str) -> NovelCreationMaterialImport | None:
    return db.get(NovelCreationMaterialImport, import_id)


def find_material_import_by_file(
    db: Session,
    *,
    session_id: str,
    file_sha256: str,
) -> NovelCreationMaterialImport | None:
    return (
        db.query(NovelCreationMaterialImport)
        .filter_by(session_id=session_id, file_sha256=file_sha256)
        .first()
    )


def list_material_import_records(db: Session, session_id: str) -> list[NovelCreationMaterialImport]:
    return (
        db.query(NovelCreationMaterialImport)
        .filter_by(session_id=session_id)
        .order_by(NovelCreationMaterialImport.created_at.desc())
        .all()
    )


def claim_material_import_retry(db: Session, import_id: str) -> bool:
    claimed = (
        db.query(NovelCreationMaterialImport)
        .filter(
            NovelCreationMaterialImport.id == import_id,
            NovelCreationMaterialImport.status.in_(("failed", "cancelled", "interrupted")),
        )
        .update(
            {
                NovelCreationMaterialImport.status: "queued",
                NovelCreationMaterialImport.error: None,
                NovelCreationMaterialImport.completed_at: None,
                NovelCreationMaterialImport.updated_at: _now(),
            },
            synchronize_session=False,
        )
    )
    return claimed == 1


def mark_interrupted_material_imports(db: Session) -> int:
    rows = db.query(NovelCreationMaterialImport).filter(NovelCreationMaterialImport.status.in_(("queued", "running", "applying"))).all()
    now = _now()
    for row in rows:
        row.status = "interrupted"
        row.error = "应用重启时导入尚未完成；分块检查点已保留"
        row.updated_at = now
    return len(rows)


__all__ = [
    "IMPORTABLE_ARTIFACTS", "MAX_UPLOAD_BYTES", "SUPPORTED_EXTENSIONS",
    "apply_material_import", "build_import_preview", "deterministic_extract_chunk",
    "claim_material_import_retry", "create_material_import", "find_material_import_by_file",
    "get_material_import_record", "list_material_import_records",
    "mark_interrupted_material_imports", "merge_import_extractions", "parse_creation_material",
    "run_material_import", "serialize_material_import", "split_creation_material",
]
