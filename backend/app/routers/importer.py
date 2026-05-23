"""File import — upload TXT/DOCX, parse, AI-split, and save as chapters."""
import asyncio
import io
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from docx import Document as DocxDocument

from ..ai.gateway import LLMGateway
from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.models import Chapter, OutlineNode, Project
from ..database.session import get_db
from ..schemas.importer import ConfirmImportRequest, ImportSplitRequest
from .chapters import _count_words

router = APIRouter(tags=["import"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
LLM_SPLIT_GROUP_SIZE = 3
LLM_SPLIT_OVERLAP = 1
CHAPTER_TITLE_RE = re.compile(
    r"(?im)^\s*("
    r"第[零一二三四五六七八九十百千万\d]+[章节回部卷](?:[^\n一-鿿][^\n]{0,39})?"
    r"|Chapter\s+\d+[^\n]{0,60}"
    r"|CHAPTER\s+\d+[^\n]{0,60}"
    r"|Part\s+\d+[^\n]{0,60}"
    r")\s*$"
)


def _get_outline_node_or_404(db: Session, project_id: str, outline_node_id: Optional[str]) -> Optional[OutlineNode]:
    if not outline_node_id:
        return None
    node = (
        db.query(OutlineNode)
        .filter(OutlineNode.id == outline_node_id, OutlineNode.project_id == project_id)
        .first()
    )
    if not node:
        raise ValidationError("关联大纲节点必须属于当前作品")
    return node


def _parse_txt(raw: bytes) -> str:
    for encoding in ("utf-8", "gbk", "gb18030", "utf-16"):
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


def _upload_file_response(project_id: str, file: UploadFile, db: Session):
    get_project_or_404(db, project_id)

    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ("txt", "docx"):
        raise ValidationError("仅支持 .txt 和 .docx 格式文件")

    raw = file.file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationError("文件太大，最大支持10MB")

    if ext == "docx":
        text = _parse_docx(raw)
    else:
        text = _parse_txt(raw)

    if not text.strip():
        raise ValidationError("文件内容为空或无法解析")

    word_count = len(text)
    return ApiResponse.success(data={
        "filename": filename,
        "format": ext,
        "text": text,
        "word_count": word_count,
        "preview": text[:500],
    }, message=f"文件解析成功，共 {word_count} 字符")


@router.post("/projects/{project_id}/import/file")
def import_file(project_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a TXT or .docx file and return its parsed text content."""
    return _upload_file_response(project_id, file, db)


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
    excerpt_start = max(0, group["start_char"] - 400)
    excerpt_end = min(len(text), group["end_char"] + 400)
    excerpt = text[excerpt_start:excerpt_end]

    messages = [
        {
            "role": "system",
            "content": (
                "你是小说导入流程中的章节边界校验助手，专精于在文本中精确定位章节分界。你的工作不是创作，而是校对——用最少的修正让规则预识别结果更准确。\n\n"
                "【边界判断原则】\n"
                "1. 章节标题行通常具有以下特征：独占一行（上下有空行或段落边界）、包含「第X章/回/卷」等序号结构、长度较短（通常不超过30字）。\n"
                "2. 上下文语义验证：如果候选标题出现在句子中间（如「这是第二回合的较量」），或前后文明显表明它不是标题（如它是人物对话的一部分），则不应将其视为章节边界。\n"
                "3. 字符位置精确度：start_char 必须指向标题行的第一个字符，end_char 必须指向该章节内容结束的位置（通常也就是下一章节标题的开始位置或全文末尾）。\n"
                "4. 重叠处理：如果同一位置附近有多个候选边界，选择最合理的一个——优先完整的标题行，其次考虑上下文的最自然断点。\n\n"
                "【不确定情况处理】\n"
                "- 如果你对一个边界是否正确没有把握，保留该边界但将 needs_review 标记为 true，并在 review_reason 中说明原因。\n"
                "- 常见的需要标记审核的情况：包含「回」「章」等字但不是章节标题（如「第二回合，阿远换了打法」——这是战斗描写而非章节标题）。\n\n"
                "【输出格式】\n"
                "只输出JSON数组，不要输出解释文字：\n"
                "[{\"title\":\"章节标题\",\"start_char\":0,\"end_char\":12345,\"preview\":\"前100字\"}]\n"
                "start_char和end_char必须是相对于全文的字符位置索引（从0开始）。如果候选边界已经正确，可以原样返回。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"文本总长度：{len(text)} 字符\n\n"
                f"当前块编号：{group['block_index']}\n"
                f"当前块全文坐标范围：{excerpt_start}-{excerpt_end}\n"
                f"规则预识别候选：\n{json.dumps(group['candidates'], ensure_ascii=False)}\n\n"
                f"当前块文本：\n{excerpt}\n\n"
                "请校正该块内章节边界并输出JSON数组。"
            ),
        },
    ]

    last_error = ""
    for attempt in range(3):
        try:
            result = await LLMGateway.chat_completion(
                messages=messages, model=model, temperature=0.2, max_tokens=2000
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


async def _llm_correct_splits_chunked(text: str, candidates: list[dict], model: Optional[str]) -> tuple[Optional[list[dict]], int]:
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


async def _build_split_preview(text: str, model: Optional[str] = None) -> tuple[list[dict], str, bool, int]:
    candidates = _normalize_splits(_regex_splits(text), text)
    needs_review = len(candidates) <= 1 and len(text) > 5000
    method = "regex" if len(candidates) > 1 else "length"
    if model:
        corrected, failed_blocks = await _llm_correct_splits_chunked(text, candidates, model)
        if corrected:
            return corrected, "regex+chunked-llm", needs_review or failed_blocks > 0, failed_blocks
    return candidates, method, needs_review, 0


@router.post("/projects/{project_id}/import/preview")
async def import_preview(
    project_id: str,
    payload: ImportSplitRequest,
    db: Session = Depends(get_db),
):
    """Return regex-first chapter split suggestions, optionally LLM-corrected."""
    get_project_or_404(db, project_id)
    splits, method, needs_review, failed_blocks = await _build_split_preview(payload.text, payload.model)
    return ApiResponse.success(data={
        "splits": splits,
        "total": len(splits),
        "method": method,
        "needs_review": needs_review,
        "failed_blocks": failed_blocks,
    }, message=f"识别到 {len(splits)} 个章节边界")


@router.post("/projects/{project_id}/import/confirm")
def confirm_import(project_id: str, payload: ConfirmImportRequest, db: Session = Depends(get_db)):
    """Save imported text as chapters based on split suggestions."""
    get_project_or_404(db, project_id)
    _get_outline_node_or_404(db, project_id, payload.outline_node_id)

    created_chapters: list[Chapter] = []
    if payload.splits:
        for i, split in enumerate(payload.splits):
            start = max(0, int(split.start_char))
            end = min(len(payload.text), int(split.end_char))
            chunk = payload.text[start:end].strip()
            if not chunk:
                continue
            word_count = _count_words(chunk)
            chapter = Chapter(
                project_id=project_id,
                title=split.title or f"导入章节 {i + 1}",
                content=chunk,
                outline_node_id=payload.outline_node_id,
                word_count=word_count,
                current_version=1,
            )
            db.add(chapter)
            created_chapters.append(chapter)
    else:
        chunk = payload.text.strip()
        if not chunk:
            raise ValidationError("没有可导入的有效内容")
        chapter = Chapter(
            project_id=project_id,
            title="导入章节",
            content=chunk,
            outline_node_id=payload.outline_node_id,
            word_count=_count_words(chunk),
            current_version=1,
        )
        db.add(chapter)
        created_chapters.append(chapter)

    if not created_chapters:
        raise ValidationError("没有可导入的有效章节")

    db.flush()
    chapters = [
        {"id": chapter.id, "title": chapter.title, "word_count": chapter.word_count or 0}
        for chapter in created_chapters
    ]
    db.commit()
    return ApiResponse.success(data={"chapters": chapters, "total": len(chapters)}, message=f"成功导入 {len(chapters)} 章")
