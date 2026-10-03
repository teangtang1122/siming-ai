"""Non-destructive, source-anchored de-AI chapter revision previews."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from ..ai.local_cli_adapter import is_local_cli_provider
from ..core.exceptions import LLMError, NotFoundError, ValidationError
from ..core.utils import count_words
from ..database.models import Chapter, Project
from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..prompts.anti_ai_prompts import (
    build_de_ai_edit_prompt,
    build_de_ai_fidelity_audit_prompt,
    build_de_ai_style_audit_prompt,
)
from ..prompts.style_prompts import build_style_context
from .de_ai_edits import apply_de_ai_edits
from .de_ai_validation import (
    assess_de_ai_revision,
    parse_de_ai_fidelity_audit,
    parse_de_ai_style_audit,
)


def _audit_runtime_failure(label: str, exc: Exception) -> dict[str, Any]:
    detail = str(exc).strip() or exc.__class__.__name__
    return {
        "valid": False,
        "passed": False,
        "issues": [{
            "chunk": 0,
            "kind": "audit_unavailable",
            "detail": f"{label}未能完成：{detail}",
        }],
    }


def _revision_preview_warnings(
    assessment: dict[str, Any],
    fidelity_audit: dict[str, Any],
    style_audit: dict[str, Any],
    rejected_edits: int,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for issue in assessment.get("issues", []):
        if isinstance(issue, dict):
            warnings.append({
                "source": "revision_quality",
                "code": str(issue.get("code") or "review_warning"),
                "detail": str(issue.get("detail") or ""),
            })
    for source, audit in (("fidelity_audit", fidelity_audit), ("style_audit", style_audit)):
        if audit.get("valid") and audit.get("passed"):
            continue
        issues = audit.get("issues") or [{
            "kind": "review_warning",
            "detail": "该项系统审核未通过，请人工对照原文确认。",
        }]
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            warning = {
                "source": source,
                "code": str(issue.get("kind") or "review_warning"),
                "detail": str(issue.get("detail") or ""),
            }
            if int(issue.get("chunk") or 0) > 0:
                warning["chunk"] = int(issue["chunk"])
            warnings.append(warning)
    if rejected_edits:
        warnings.append({
            "source": "revision_quality",
            "code": "unapplied_edits",
            "detail": f"模型另有 {rejected_edits} 处修订未准确对应原文或删减过多，已跳过。",
        })
    return warnings


def _load_revision_context(
    db: Session,
    project_id: str,
    chapter_id: str | None,
    *,
    content: str,
    original_content: str | None,
    revision_round: int,
) -> tuple[Project, Chapter | None, str, str, int]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")
    chapter = (
        db.query(Chapter)
        .filter(Chapter.id == chapter_id, Chapter.project_id == project_id)
        .first()
    ) if chapter_id else None
    if chapter_id and not chapter:
        raise NotFoundError("章节不存在")

    source = str(content or "")
    try:
        round_number = int(revision_round)
    except (TypeError, ValueError) as exc:
        raise ValidationError("去除 AI 味轮次必须是 1 到 3") from exc
    if not 1 <= round_number <= 3:
        raise ValidationError("去除 AI 味最多允许处理 3 轮")
    if round_number > 1 and original_content is None:
        raise ValidationError("第 2/3 轮必须提供最初原文，防止连续处理造成故事漂移")
    original = str(original_content if original_content is not None else source)
    if len(source.strip()) < 20 or len(original.strip()) < 20:
        raise ValidationError("正文太短，至少需要 20 个字符才能去除 AI 味")
    return project, chapter, source, original, round_number


async def preview_de_ai_revision(
    db: Session,
    project_id: str,
    chapter_id: str | None,
    *,
    content: str,
    original_content: str | None = None,
    revision_round: int = 1,
    model: str | None = None,
) -> dict[str, Any]:
    """Preview local edits; never mutate a saved chapter or unsaved draft."""
    project, chapter, source, original, round_number = _load_revision_context(
        db,
        project_id,
        chapter_id,
        content=content,
        original_content=original_content,
        revision_round=revision_round,
    )
    provider = LLMGateway.provider_for_model(model)
    local_cli = is_local_cli_provider(provider)
    request_body: dict[str, Any] = {
        "moshu_task_type": "writing",
        "moshu_project_id": project_id,
    }
    if local_cli:
        request_body.update({
            "local_cli_isolated": True,
            "local_cli_timeout_seconds": 600,
        })
    extra_body = LLMGateway.local_cli_extra_body(model, base=request_body)
    style_context = build_style_context(project, include_anti_ai=False, concise=True)
    generation_timeout = 600 if local_cli else 240
    try:
        result = await LLMGateway.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是中文小说编辑。只做局部修订，只输出合法JSON对象。"
                        "原文未列入修订的文字必须原样保留。\n"
                        f"【作品文风】\n{style_context}"
                    ),
                },
                {"role": "user", "content": build_de_ai_edit_prompt(source)},
            ],
            model=model,
            temperature=0.35,
            max_tokens=2_000,
            timeout=generation_timeout,
            retry=1,
            extra_body=extra_body,
        )
        rewritten, applied_edits, rejected_edits = apply_de_ai_edits(
            source, result.get("content")
        )
        assessment = assess_de_ai_revision(
            original, rewritten, require_substantial_revision=False
        )
        if any(
            issue.get("code") in {
                "excessive_shrinkage",
                "chapter_word_count_floor",
                "excessive_expansion",
            }
            for issue in assessment["issues"]
        ):
            raise LLMError("去除 AI 味失败：候选稿篇幅偏离原文，已拒绝；原文未变")
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"去除 AI 味失败：{exc}") from exc

    audit_timeout = 180 if local_cli else 90

    async def audit(label: str, system: str, prompt: str, parser: Any) -> dict[str, Any]:
        try:
            response = await asyncio.wait_for(
                LLMGateway.chat_completion(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    model=model,
                    temperature=0,
                    max_tokens=650,
                    timeout=audit_timeout,
                    retry=1,
                    extra_body=extra_body,
                ),
                timeout=audit_timeout,
            )
            return parser(response.get("content"), chunk_count=1)
        except Exception as exc:
            return _audit_runtime_failure(label, exc)

    fidelity_audit, style_audit = await asyncio.gather(
        audit(
            "故事保真审计",
            "你是小说事实校对员。只输出合法JSON对象。",
            build_de_ai_fidelity_audit_prompt(original, [rewritten]),
            parse_de_ai_fidelity_audit,
        ),
        audit(
            "表达结构审计",
            "你是小说表达审校员。只输出合法JSON对象。",
            build_de_ai_style_audit_prompt([rewritten]),
            parse_de_ai_style_audit,
        ),
    )
    warnings = _revision_preview_warnings(
        assessment, fidelity_audit, style_audit, rejected_edits
    )
    request_meta = result.get("request_meta") if isinstance(result.get("request_meta"), dict) else {}
    return {
        "chapter_id": chapter.id if chapter else None,
        "original": original,
        "input": source,
        "rewritten": rewritten,
        "original_word_count": count_words(original),
        "input_word_count": count_words(source),
        "rewritten_word_count": count_words(rewritten),
        "provider": str(request_meta.get("provider") or provider or ""),
        "model": str(request_meta.get("model") or result.get("model") or model or ""),
        "mutated": False,
        "persisted": False,
        "auto_adopted": False,
        "review_required": True,
        "revision_round": round_number,
        "max_revision_rounds": 3,
        "can_continue": round_number < 3,
        "audit_passed": not warnings,
        "candidate_status": "ready" if not warnings else "review_with_warnings",
        "warnings": warnings,
        "revision_quality": assessment,
        "fidelity_audit": fidelity_audit,
        "style_audit": style_audit,
        "applied_edit_count": applied_edits,
        "rejected_edit_count": rejected_edits,
    }
