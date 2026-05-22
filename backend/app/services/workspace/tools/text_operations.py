"""Text rewrite, expand, and continuation workspace tools."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....database.models import Project
from ....prompts.text_operations import (
    build_continue_messages,
    build_expand_messages,
    build_rewrite_messages,
)
from ....services.context_builders import (
    _build_outline_context,
    _build_recent_summaries,
)
from ....services.style_rules import (
    STYLE_PROMPTS,
    _build_style_context,
    _repair_forbidden_sentence_text,
)


async def rewrite_text(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        return {"tool": "rewrite_text", "status": "skipped", "detail": "缺少要改写的文本（text）", "data": {}}

    style = str(args.get("style") or "").strip() or None
    prompt = str(args.get("prompt") or "").strip() or None

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "rewrite_text", "status": "skipped", "detail": "项目不存在", "data": {}}

    style_ctx = _build_style_context(project)
    style_instruction = STYLE_PROMPTS.get(style, "") if style else ""

    messages = build_rewrite_messages(
        style_context=style_ctx,
        style_instruction=style_instruction,
        prompt=prompt,
        text=text,
    )

    model = str(args.get("model") or "") or None
    temperature = float(args.get("temperature") or 0.7)
    max_tokens = int(args.get("max_tokens") or 0) or None

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "rewrite_text", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": {}}

    rewritten, violations, remaining = await _repair_forbidden_sentence_text(
        result.get("content", ""),
        project,
        model,
        max_tokens,
    )

    return {
        "tool": "rewrite_text",
        "status": "ok",
        "detail": "文本改写完成",
        "data": {
            "original": text[:500],
            "rewritten": rewritten,
            "style": style,
            "style_violations": violations,
            "style_remaining_violations": remaining,
        },
    }


async def expand_text(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        return {"tool": "expand_text", "status": "skipped", "detail": "缺少要扩写的文本（text）", "data": {}}

    prompt = str(args.get("prompt") or "").strip() or None

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "expand_text", "status": "skipped", "detail": "项目不存在", "data": {}}

    style_ctx = _build_style_context(project)

    messages = build_expand_messages(
        style_context=style_ctx,
        prompt=prompt,
        text=text,
    )

    model = str(args.get("model") or "") or None
    temperature = float(args.get("temperature") or 0.7)
    max_tokens = int(args.get("max_tokens") or 0) or None

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "expand_text", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": {}}

    expanded, violations, remaining = await _repair_forbidden_sentence_text(
        result.get("content", ""),
        project,
        model,
        max_tokens,
    )

    return {
        "tool": "expand_text",
        "status": "ok",
        "detail": "文本扩写完成",
        "data": {
            "original": text[:500],
            "expanded": expanded,
            "style_violations": violations,
            "style_remaining_violations": remaining,
        },
    }


async def continue_text(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        return {"tool": "continue_text", "status": "skipped", "detail": "缺少上文文本（text）", "data": {}}

    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    prompt = str(args.get("prompt") or "").strip() or None

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "continue_text", "status": "skipped", "detail": "项目不存在", "data": {}}

    style_ctx = _build_style_context(project)
    summaries = _build_recent_summaries(db, project_id, limit=5)
    outline_ctx = _build_outline_context(db, project_id, outline_node_id) if outline_node_id else "无指定大纲节点。"

    messages = build_continue_messages(
        style_context=style_ctx,
        outline_context=outline_ctx,
        summaries=summaries,
        prompt=prompt,
        text=text,
    )

    model = str(args.get("model") or "") or None
    temperature = float(args.get("temperature") or 0.7)
    max_tokens = int(args.get("max_tokens") or 0) or None

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "continue_text", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": {}}

    continuation, violations, remaining = await _repair_forbidden_sentence_text(
        result.get("content", ""),
        project,
        model,
        max_tokens,
    )

    return {
        "tool": "continue_text",
        "status": "ok",
        "detail": "续写完成",
        "data": {
            "previous": text[-200:] if len(text) > 200 else text,
            "continuation": continuation,
            "style_violations": violations,
            "style_remaining_violations": remaining,
        },
    }
