"""TXT/Markdown/DOCX import service: parse files and create chapter rows."""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from fastapi import UploadFile
from sqlalchemy.orm import Session

from ..core.exceptions import ValidationError
from ..core.utils import count_words
from ..database.models import Chapter
from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..prompts.import_prompts import build_split_correction_messages
from .chapter_ordering import CHAPTER_ORDER_STEP, next_chapter_sort_order

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MiB
MAX_IMPORT_CHAPTERS = 2_000
LLM_SPLIT_GROUP_SIZE = 3
LLM_SPLIT_OVERLAP = 1
SUPPORTED_IMPORT_EXTENSIONS = {"txt", "md", "docx"}
CHAPTER_TITLE_RE = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]+)?("
    r"(?:【[ \t]*)?"
    r"(?:"
    r"第[ \t]*[零〇一二三四五六七八九十百千万\d]+[ \t]*[章节部卷]"
    r"|(?:卷|部)[ \t]*[零〇一二三四五六七八九十百千万\d]+"
    r"|Chapter[ \t]+\d+"
    r"|Part[ \t]+\d+"
    r"|序章|楔子|引子|尾声"
    r")"
    r"(?:[^\r\n]{0,60})?"
    r"(?:[ \t]*】)?"
    r")[ \t]*$"
)
CHAPTER_PREFIX_RE = re.compile(
    r"(?i)^(?:"
    r"第[ \t]*[零〇一二三四五六七八九十百千万\d]+[ \t]*[章节部卷]"
    r"|(?:卷|部)[ \t]*[零〇一二三四五六七八九十百千万\d]+"
    r"|Chapter[ \t]+\d+"
    r"|Part[ \t]+\d+"
    r"|序章|楔子|引子|尾声"
    r")"
)
_CHAPTER_TITLE_SEPARATORS = set(" \t：:-—·_")
_CHAPTER_SENTENCE_ENDINGS = set("。！？!?；;，,")


def _is_likely_chapter_title(value: str) -> bool:
    raw = str(value or "").strip()
    bracketed = raw.startswith("【") and raw.endswith("】")
    core = raw.removeprefix("【").removesuffix("】").strip()
    prefix = CHAPTER_PREFIX_RE.match(core)
    if prefix is None:
        return False
    if bracketed:
        return True
    suffix = core[prefix.end() :]
    if not suffix.strip():
        return True
    return (
        suffix[0] in _CHAPTER_TITLE_SEPARATORS
        or suffix.rstrip()[-1] not in _CHAPTER_SENTENCE_ENDINGS
    )


def _text_quality(text: str) -> float:
    if not text:
        return -10.0
    sample = text[:20_000]
    printable = 0
    cjk = 0
    bad = 0
    for char in sample:
        code = ord(char)
        if char in {"\ufffd", "\x00"}:
            bad += 8
        elif (code < 32 and char not in "\n\r\t") or 0x7F <= code <= 0x9F:
            bad += 4
        elif char.isprintable() or char in "\n\r\t":
            printable += 1
        else:
            bad += 2
        if 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
            cjk += 1
    size = max(1, len(sample))
    return printable / size + min(cjk / size, 0.25) * 0.2 - bad / size


def _strict_decode(raw: bytes, encoding: str) -> str | None:
    try:
        return raw.decode(encoding, errors="strict")
    except (UnicodeDecodeError, LookupError):
        return None


def _looks_like_utf16(raw: bytes) -> bool:
    sample = raw[: min(len(raw) - len(raw) % 2, 8_192)]
    if len(sample) < 4:
        return False
    pairs = len(sample) // 2
    even_zeros = sum(1 for index in range(0, len(sample), 2) if sample[index] == 0)
    odd_zeros = sum(1 for index in range(1, len(sample), 2) if sample[index] == 0)
    threshold = max(3, pairs // 12)
    return even_zeros >= threshold or odd_zeros >= threshold


def _decode_txt(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        decoded = _strict_decode(raw[3:], "utf-8")
        if decoded is None:
            raise ValidationError("UTF-8 BOM 文件内容损坏")
        return decoded, "UTF-8 BOM"
    if raw.startswith(b"\xff\xfe"):
        decoded = _strict_decode(raw[2:], "utf-16-le")
        if decoded is None:
            raise ValidationError("UTF-16LE 文件内容损坏")
        return decoded, "UTF-16LE"
    if raw.startswith(b"\xfe\xff"):
        decoded = _strict_decode(raw[2:], "utf-16-be")
        if decoded is None:
            raise ValidationError("UTF-16BE 文件内容损坏")
        return decoded, "UTF-16BE"

    utf8 = _strict_decode(raw, "utf-8")
    if utf8 is not None and _text_quality(utf8) >= 0.90:
        return utf8.removeprefix("\ufeff"), "UTF-8"

    candidates: list[tuple[float, str, str]] = []
    if utf8 is not None:
        candidates.append((_text_quality(utf8), "UTF-8", utf8))

    if len(raw) % 2 == 0 and _looks_like_utf16(raw):
        for encoding, label in (("utf-16-le", "UTF-16LE"), ("utf-16-be", "UTF-16BE")):
            decoded = _strict_decode(raw, encoding)
            if decoded is not None:
                candidates.append((_text_quality(decoded), label, decoded))

    simplified_hints = set("这为国后发里时会来个们说对从实还进")
    traditional_hints = set("這為國後發裡時會來個們說對從實還進")
    for encoding, label, hints in (
        ("gb18030", "GB18030", simplified_hints),
        ("big5", "Big5", traditional_hints),
    ):
        decoded = _strict_decode(raw, encoding)
        if decoded is None:
            continue
        bonus = min(sum(1 for char in decoded[:20_000] if char in hints), 100) / 10_000
        candidates.append((_text_quality(decoded) + bonus, label, decoded))

    if not candidates:
        raise ValidationError("无法识别 TXT 编码，请先另存为 UTF-8 或 GB18030")
    score, label, decoded = max(candidates, key=lambda item: item[0])
    if score < 0.72:
        raise ValidationError("无法可靠识别 TXT 编码，请先另存为 UTF-8、GB18030 或 UTF-16")
    return decoded.removeprefix("\ufeff"), label


def _parse_txt(raw: bytes) -> str:
    """Backward-compatible text-only parser."""
    return _decode_txt(raw)[0]


def _parse_docx(raw: bytes) -> str:
    buf = io.BytesIO(raw)
    doc = DocxDocument(buf)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _parse_raw_file(filename: str, raw: bytes) -> dict:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_IMPORT_EXTENSIONS:
        raise ValidationError("仅支持 .txt、.md 和 .docx 格式文件")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationError("文件太大，最大支持 20 MiB")

    if ext == "docx":
        text = _parse_docx(raw)
        detected_encoding = "DOCX"
    else:
        text, detected_encoding = _decode_txt(raw)
    if not text.strip():
        raise ValidationError("文件内容为空或无法解析")

    return {
        "filename": filename,
        "format": ext,
        "encoding": detected_encoding,
        "text": text,
        "word_count": count_words(text),
        "preview": text[:500],
    }


def parse_uploaded_file(file: UploadFile) -> dict:
    """Parse an uploaded TXT/Markdown/DOCX file and return text metadata."""
    filename = file.filename or ""
    raw = file.file.read()
    return _parse_raw_file(filename, raw)


def parse_local_file(file_path: str) -> dict:
    """Parse a local TXT/Markdown/DOCX file for workspace/MCP import tools."""
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
        return [
            {"title": "导入章节", "start_char": 0, "end_char": len(text), "preview": text[:100]}
        ]

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
            splits.append(
                {
                    "title": f"导入章节 {index}",
                    "start_char": start,
                    "end_char": end,
                    "preview": chunk[:100],
                }
            )
            index += 1
        start = max(end, start + 1)
    return splits


def _regex_splits(text: str) -> list[dict]:
    matches = [
        match
        for match in CHAPTER_TITLE_RE.finditer(text)
        if _is_likely_chapter_title(match.group(1))
    ]
    if not matches:
        return _fallback_splits(text)

    splits = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if not chunk:
            continue
        splits.append(
            {
                "title": match.group(1).strip()[:100],
                "start_char": start,
                "end_char": end,
                "preview": chunk[:100],
            }
        )
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
        normalized.append(
            {
                "title": str(split.get("title") or f"导入章节 {index + 1}")[:100],
                "start_char": start,
                "end_char": end,
                "preview": str(split.get("preview") or chunk[:100])[:200],
                "needs_review": bool(split.get("needs_review", False)),
                "review_reason": split.get("review_reason"),
                "source": split.get("source"),
                "block_index": split.get("block_index"),
            }
        )
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
        groups.append(
            {
                "block_index": len(groups),
                "candidate_start": start,
                "candidate_end": end,
                "candidates": group_candidates,
                "start_char": min(item["start_char"] for item in group_candidates),
                "end_char": max(item["end_char"] for item in group_candidates),
            }
        )
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
            parsed = json.loads(
                splits_text.strip().removeprefix("```json").removesuffix("```").strip()
            )
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
    model: str | None,
) -> tuple[list[dict] | None, int]:
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


async def build_split_preview(
    text: str, model: str | None = None
) -> tuple[list[dict], str, bool, int]:
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
    outline_node_id: str | None = None,
) -> list[dict]:
    """Create Chapter rows from split definitions and return summaries."""
    created_chapters: list[Chapter] = []
    next_sort_order = next_chapter_sort_order(db, project_id)
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
                sort_order=next_sort_order + len(created_chapters) * CHAPTER_ORDER_STEP,
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
            sort_order=next_sort_order,
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
