"""Workspace tools for the resumable V2 novel creation workbench."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....core.json_repair import parse_json_object
from ....database.models import NovelCreationSession, NovelCreationStageRun
from ...novel_creation_workspace import (
    STAGE_LABELS,
    STAGE_ORDER,
    add_run_event,
    complete_run,
    create_run,
    derive_stage,
    fail_run,
    patch_session,
    save_stage,
    serialize_run,
    serialize_session,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _session(db: Session, session_id: str) -> NovelCreationSession | None:
    return db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()


def _stage_contract(stage: str) -> str:
    contracts = {
        "world_style": "保留 writing_style/world_tone/story_structure/pacing/style_rules/forbidden_patterns/worldbuilding/display_groups 字段；worldbuilding 使用司命六维分类。",
        "characters": "返回 characters 和 relationships。每个角色保留年龄、外貌、位置、状态，并包含 profile 的 core_motivation、inner_lack、core_belief、public_persona、hidden_persona、reveal_chapter、moral_taboo、voice、action_habit、trauma_trigger。",
        "locations": "返回 entries 和 relations。关系必须含 source_title、target_title、relation_type、description、metadata。",
        "macro_outline": "返回 story_overview、core_conflict、ending_direction、target_chapters、volumes、stage_plan；只做全书宏观结构，不展开全部章节。",
        "opening_outline": "恰好返回前15章 chapters；每章返回2至6个 sections。section 必须含 parent_client_id 及 metadata.scene_number/purpose/location/timeline/pov_character/characters/entry_state/exit_state/emotional_residue/unresolved_actions。",
        "final_review": "返回 ready、blocking、warnings、counts。只根据证据审阅，不擅自删改上游内容。",
    }
    return contracts.get(stage, "保持输入结构，只提高具体性、一致性和可执行性。")


def _validate_stage(stage: str, data: dict[str, Any]) -> None:
    if not isinstance(data, dict) or not data:
        raise ValueError("模型没有返回可用的阶段对象")
    if stage == "opening_outline":
        chapters = data.get("chapters") if isinstance(data.get("chapters"), list) else []
        sections = data.get("sections") if isinstance(data.get("sections"), list) else []
        if len(chapters) != 15:
            raise ValueError(f"前15章细纲必须恰好包含15章，当前为{len(chapters)}章")
        counts: dict[str, int] = {}
        for section in sections:
            if isinstance(section, dict):
                parent = _text(section.get("parent_client_id"))
                counts[parent] = counts.get(parent, 0) + 1
        invalid = [chapter.get("client_id") for chapter in chapters if counts.get(_text(chapter.get("client_id")), 0) not in range(2, 7)]
        if invalid:
            raise ValueError("以下章节的 section 数量不在2至6之间：" + "、".join(_text(item) for item in invalid[:5]))


async def _enhance_with_model(
    session: NovelCreationSession,
    stage: str,
    baseline: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    draft = session.draft_json if isinstance(session.draft_json, dict) else {}
    context = {
        "form": draft.get("form"),
        "selected_concept_id": draft.get("selected_concept_id"),
        "confirmed_stages": {
            name: value.get("data")
            for name, value in (draft.get("stages") or {}).items()
            if isinstance(value, dict) and value.get("status") == "confirmed"
        },
        "baseline": baseline,
    }
    system = (
        "你是司命的新书立项编辑。你只处理当前阶段，不提前创建正式项目，也不写文件。"
        "必须返回一个 JSON 对象，顶层只有 data 字段。所有结论应能被作者编辑。"
        "不要用 Markdown，不要省略必填字段。"
    )
    user = (
        f"当前阶段：{STAGE_LABELS.get(stage, stage)}\n"
        f"结构契约：{_stage_contract(stage)}\n"
        "请在保留作者约束和已确认事实的前提下，深化 baseline；不要改变已经确认的专名。\n"
        f"上下文：{json.dumps(context, ensure_ascii=False)}"
    )
    from ....services.content_store import content_root

    result = await LLMGateway.chat_completion(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=model,
        temperature=0.65,
        max_tokens=12000 if stage == "opening_outline" else 6000,
        timeout=180,
        retry=0,
        extra_body=LLMGateway.local_cli_extra_body(
            model,
            cwd=str(content_root()),
            base={"moshu_task_type": "planning", "storage_target": "session_draft"},
        ),
    )
    raw = _text(result.get("content")) if isinstance(result, dict) else ""
    if not raw:
        raise RuntimeError("没有收到模型的文字回复")
    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("模型返回的阶段 JSON 格式不合法")
    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
    _validate_stage(stage, data)
    return data


async def get_novel_creation_session(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session_id = _text(args.get("session_id"))
    session = _session(db, session_id)
    if not session:
        return {"tool": "get_novel_creation_session", "status": "skipped", "detail": "Session not found", "data": None}
    return {
        "tool": "get_novel_creation_session",
        "status": "ok",
        "detail": "Novel creation session loaded",
        "data": serialize_session(session),
    }


async def generate_novel_creation_stage(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session_id = _text(args.get("session_id"))
    stage = _text(args.get("stage"))
    session = _session(db, session_id)
    if not session:
        return {"tool": "generate_novel_creation_stage", "status": "skipped", "detail": "Session not found", "data": None}
    if stage not in {*STAGE_ORDER, "all"}:
        return {"tool": "generate_novel_creation_stage", "status": "skipped", "detail": "Unknown stage", "data": None}

    if isinstance(args.get("session_patch"), dict):
        patch_session(session, args["session_patch"])
    model = _text(args.get("model"))
    use_model = bool(args.get("use_model", bool(model)))
    auto_confirm = bool(args.get("auto_confirm", stage == "all"))
    existing_run_id = _text(args.get("_run_id"))
    run = db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == existing_run_id).first() if existing_run_id else None
    if run is None:
        run = create_run(db, session, stage, args)
        db.commit()

    try:
        stages = [name for name in STAGE_ORDER if name not in {"constraints", "concepts", "final_review"}] if stage == "all" else [stage]
        generated: dict[str, Any] = {}
        for name in stages:
            add_run_event(
                db,
                run,
                "stage_progress",
                "running",
                f"正在生成{STAGE_LABELS.get(name, name)}",
                {"stage": name, "model_source": model or "contract", "storage_target": "session_draft"},
            )
            run.current_message = f"正在生成{STAGE_LABELS.get(name, name)}"
            db.commit()
            baseline = derive_stage(session, name)
            data = await _enhance_with_model(session, name, baseline, model) if use_model and model and name != "final_review" else baseline
            _validate_stage(name, data)
            save_stage(session, name, data, confirm=auto_confirm, source="model" if use_model and model else "contract")
            generated[name] = deepcopy(data)
            add_run_event(
                db,
                run,
                "stage_completed",
                "ok",
                f"{STAGE_LABELS.get(name, name)}已保存",
                {"stage": name, "storage_target": "session_draft"},
            )
            db.commit()
        if stage == "all":
            final = derive_stage(session, "final_review")
            save_stage(session, "final_review", final, confirm=False, source="contract")
            generated["final_review"] = final
            add_run_event(
                db,
                run,
                "stage_completed",
                "ok" if final.get("ready") else "warning",
                "最终审阅已完成",
                {"stage": "final_review", "ready": bool(final.get("ready")), "storage_target": "session_draft"},
            )
            db.commit()
        complete_run(db, run, {"stages": generated})
        db.commit()
        db.refresh(run)
        return {
            "tool": "generate_novel_creation_stage",
            "status": "ok",
            "detail": "Novel creation stage generated",
            "data": {"run": serialize_run(run), "session": serialize_session(session)},
        }
    except Exception as exc:
        db.rollback()
        session = _session(db, session_id)
        run = db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == run.id).first()
        if run and session:
            fail_run(db, run, exc)
            db.commit()
            return {
                "tool": "generate_novel_creation_stage",
                "status": "error",
                "detail": str(exc),
                "data": {"run": serialize_run(run), "session": serialize_session(session)},
            }
        return {"tool": "generate_novel_creation_stage", "status": "error", "detail": str(exc), "data": None}


async def submit_novel_creation_stage(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session_id = _text(args.get("session_id"))
    stage = _text(args.get("stage"))
    session = _session(db, session_id)
    if not session:
        return {"tool": "submit_novel_creation_stage", "status": "skipped", "detail": "Session not found", "data": None}
    if stage not in STAGE_ORDER:
        return {"tool": "submit_novel_creation_stage", "status": "skipped", "detail": "Unknown stage", "data": None}
    data = args.get("data")
    if not isinstance(data, dict):
        data = derive_stage(session, stage)
    try:
        _validate_stage(stage, data)
        save_stage(session, stage, data, confirm=bool(args.get("confirm", True)), source=_text(args.get("source")) or "author")
        db.commit()
        return {
            "tool": "submit_novel_creation_stage",
            "status": "ok",
            "detail": f"{STAGE_LABELS[stage]}已保存",
            "data": serialize_session(session),
        }
    except Exception as exc:
        db.rollback()
        return {"tool": "submit_novel_creation_stage", "status": "error", "detail": str(exc), "data": None}
