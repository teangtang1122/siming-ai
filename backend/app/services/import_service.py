"""TXT/DOCX import service: parse files, split chapters, and create chapter rows."""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from docx import Document as DocxDocument
from fastapi import UploadFile
from sqlalchemy.orm import Session

from ..core.utils import count_words

from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..core.exceptions import ValidationError
from ..core.utils import count_words
from ..database.models import Chapter
from ..prompts.import_prompts import build_split_correction_messages

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
LLM_SPLIT_GROUP_SIZE = 3
LLM_SPLIT_OVERLAP = 1
SUPPORTED_IMPORT_EXTENSIONS = {"txt", "docx"}
CHAPTER_TITLE_RE = re.compile(
    r"(?im)^\s*("
    r"第[零〇一二三四五六七八九十百千万\d]+[章节部卷](?:[^\n]{0,60})?"
    r"|第\s*[0-9]+\s*[章节部卷](?:[^\n]{0,60})?"
    r"|Chapter\s+\d+[^\n]{0,60}"
    r"|CHAPTER\s+\d+[^\n]{0,60}"
    r"|Part\s+\d+[^\n]{0,60}"
    r")\s*$"
)


def _parse_txt(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_docx(raw: bytes) -> str:
    buf = io.BytesIO(raw)
    doc = DocxDocument(buf)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _parse_raw_file(filename: str, raw: bytes) -> dict:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_IMPORT_EXTENSIONS:
        raise ValidationError("仅支持 .txt 和 .docx 格式文件")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationError("文件太大，最大支持 10MB")

    text = _parse_docx(raw) if ext == "docx" else _parse_txt(raw)
    if not text.strip():
        raise ValidationError("文件内容为空或无法解析")

    return {
        "filename": filename,
        "format": ext,
        "text": text,
        "word_count": count_words(text),
        "preview": text[:500],
    }


def parse_uploaded_file(file: UploadFile) -> dict:
    """Parse an uploaded TXT/DOCX file and return text payload metadata."""
    filename = file.filename or ""
    raw = file.file.read()
    return _parse_raw_file(filename, raw)


def parse_local_file(file_path: str) -> dict:
    """Parse a local TXT/DOCX file by path for workspace/MCP import tools."""
    expanded = os.path.expandvars(str(file_path or "").strip())
    path = Path(expanded).expanduser()
    if not path.exists() or not path.is_file():
        raise ValidationError(f"文件不存在：{file_path}")
    data = _parse_raw_file(path.name, path.read_bytes())
    data["path"] = str(path)
    return data


def _fallback_splits(text: str, chunk_size: int = 5000) -> list[dict]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [{"title": "导入章节", "start_char": 0, "end_char": len(text), "preview": text[:100]}]

    splits = []
    start = 0
    index = 1
    while start < len(text):
        end = min(len(text), start + chunk_size)
        if end < len(text):
            boundary = text.rfind("\n", start + chunk_size // 2, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            splits.append({
                "title": f"导入章节 {index}",
                "start_char": start,
                "end_char": end,
                "preview": chunk[:100],
            })
            index += 1
        start = max(end, start + 1)
    return splits


def _regex_splits(text: str) -> list[dict]:
    matches = list(CHAPTER_TITLE_RE.finditer(text))
    if not matches:
        return _fallback_splits(text)

    splits = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if not chunk:
            continue
        splits.append({
            "title": match.group(1).strip()[:100],
            "start_char": start,
            "end_char": end,
            "preview": chunk[:100],
        })
    return splits or _fallback_splits(text)


def _normalize_splits(raw_splits: list, text: str) -> list[dict]:
    normalized = []
    for index, split in enumerate(raw_splits):
        if not isinstance(split, dict):
            continue
        start = max(0, min(len(text), int(split.get("start_char", 0) or 0)))
        end = max(0, min(len(text), int(split.get("end_char", len(text)) or len(text))))
        if end <= start:
            continue
        chunk = text[start:end].strip()
        if not chunk:
            continue
        normalized.append({
            "title": str(split.get("title") or f"导入章节 {index + 1}")[:100],
            "start_char": start,
            "end_char": end,
            "preview": str(split.get("preview") or chunk[:100])[:200],
            "needs_review": bool(split.get("needs_review", False)),
            "review_reason": split.get("review_reason"),
            "source": split.get("source"),
            "block_index": split.get("block_index"),
        })
    normalized.sort(key=lambda item: item["start_char"])
    return normalized


def _split_candidate_groups(candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    groups = []
    start = 0
    while start < len(candidates):
        end = min(len(candidates), start + LLM_SPLIT_GROUP_SIZE)
        group_candidates = candidates[start:end]
        groups.append({
            "block_index": len(groups),
            "candidate_start": start,
            "candidate_end": end,
            "candidates": group_candidates,
            "start_char": min(item["start_char"] for item in group_candidates),
            "end_char": max(item["end_char"] for item in group_candidates),
        })
        if end >= len(candidates):
            break
        start = max(end - LLM_SPLIT_OVERLAP, start + 1)
    return groups


def _mark_group_for_review(group: dict, reason: str) -> list[dict]:
    marked = []
    for split in group["candidates"]:
        item = dict(split)
        item["needs_review"] = True
        item["review_reason"] = reason
        item["source"] = item.get("source") or "regex"
        item["block_index"] = group["block_index"]
        marked.append(item)
    return marked


async def _llm_correct_split_group(
    text: str,
    group: dict,
    model: str,
    retry_delays: tuple[int, int, int] = (1, 2, 4),
) -> dict:
    messages = build_split_correction_messages(text, group)

    last_error = ""
    for attempt in range(3):
        try:
            result = await LLMGateway.chat_completion(
                messages=messages,
                model=model,
                temperature=0.2,
                max_tokens=2000,
            )
            splits_text = result.get("content", "")
            parsed = json.loads(splits_text.strip().removeprefix("```json").removesuffix("```").strip())
            normalized = _normalize_splits(parsed if isinstance(parsed, list) else [], text)
            if normalized:
                for item in normalized:
                    item["needs_review"] = bool(item.get("needs_review", False))
                    item["source"] = "llm"
                    item["block_index"] = group["block_index"]
                return {"block_index": group["block_index"], "splits": normalized, "failed": False}
            last_error = "LLM 未返回有效章节边界"
        except Exception as exc:
            last_error = str(exc)
        if attempt < 2:
            await asyncio.sleep(retry_delays[attempt])
    return {
        "block_index": group["block_index"],
        "splits": _mark_group_for_review(group, last_error or "LLM 校正失败"),
        "failed": True,
        "error": last_error,
    }


def _merge_chunked_splits(results: list[dict]) -> tuple[list[dict], int]:
    merged: list[dict] = []
    failed_blocks = 0
    by_range: dict[tuple[int, int], dict] = {}
    for result in sorted(results, key=lambda item: item["block_index"]):
        if result.get("failed"):
            failed_blocks += 1
        for split in result.get("splits", []):
            key = (split["start_char"], split["end_char"])
            existing = by_range.get(key)
            if existing is None or (existing.get("needs_review") and not split.get("needs_review")):
                by_range[key] = split

    for split in by_range.values():
        if split["end_char"] > split["start_char"]:
            merged.append(split)
    merged.sort(key=lambda item: item["start_char"])
    return merged, failed_blocks


async def _llm_correct_splits_chunked(
    text: str,
    candidates: list[dict],
    model: Optional[str],
) -> tuple[Optional[list[dict]], int]:
    if not model:
        return None, 0
    groups = _split_candidate_groups(candidates)
    if not groups:
        return None, 0
    results = await asyncio.gather(
        *[_llm_correct_split_group(text, group, model) for group in groups]
    )
    merged, failed_blocks = _merge_chunked_splits(results)
    return (merged or None), failed_blocks


async def build_split_preview(text: str, model: Optional[str] = None) -> tuple[list[dict], str, bool, int]:
    candidates = _normalize_splits(_regex_splits(text), text)
    needs_review = len(candidates) <= 1 and len(text) > 5000
    method = "regex" if len(candidates) > 1 else "length"
    if model:
        corrected, failed_blocks = await _llm_correct_splits_chunked(text, candidates, model)
        if corrected:
            return corrected, "regex+chunked-llm", needs_review or failed_blocks > 0, failed_blocks
    return candidates, method, needs_review, 0


def _split_attr(split: Any, key: str, default: Any = None) -> Any:
    if isinstance(split, dict):
        return split.get(key, default)
    return getattr(split, key, default)


def execute_import(
    db: Session,
    project_id: str,
    text: str,
    splits: list,
    outline_node_id: Optional[str] = None,
) -> list[dict]:
    """Create Chapter rows from split definitions and return summaries."""
    created_chapters: list[Chapter] = []
    if splits:
        for i, split in enumerate(splits):
            start = max(0, int(_split_attr(split, "start_char", 0) or 0))
            end = min(len(text), int(_split_attr(split, "end_char", len(text)) or len(text)))
            chunk = text[start:end].strip()
            if not chunk:
                continue
            chapter = Chapter(
                project_id=project_id,
                title=str(_split_attr(split, "title", "") or f"导入章节 {i + 1}")[:200],
                content=chunk,
                outline_node_id=outline_node_id,
                word_count=count_words(chunk),
                current_version=1,
            )
            db.add(chapter)
            created_chapters.append(chapter)
    else:
        chunk = text.strip()
        if not chunk:
            raise ValidationError("没有可导入的有效内容")
        chapter = Chapter(
            project_id=project_id,
            title="导入章节",
            content=chunk,
            outline_node_id=outline_node_id,
            word_count=count_words(chunk),
            current_version=1,
        )
        db.add(chapter)
        created_chapters.append(chapter)

    if not created_chapters:
        raise ValidationError("没有可导入的有效章节")

    db.flush()
    return [
        {"id": chapter.id, "title": chapter.title, "word_count": chapter.word_count or 0}
        for chapter in created_chapters
    ]
