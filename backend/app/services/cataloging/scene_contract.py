"""Shared scene ordinals and explicit retirement of staged scene candidates."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import (
    CatalogingCandidate,
    CatalogingChapterRun,
)
from ..story_granularity import SECTION_SCENE_STATE_FIELDS, normalize_node_type
from .candidate_io import candidate_payload


def plan_scenes(db: Session, run: CatalogingChapterRun) -> list[Any] | None:
    summary = db.query(CatalogingCandidate).filter_by(
        chapter_run_id=run.id, item_type="chapter_summary",
    ).filter(CatalogingCandidate.status != "rejected").first()
    if summary is None:
        return None
    scenes = candidate_payload(summary).get("scenes")
    return scenes if isinstance(scenes, list) else None


def plan_scene_count(db: Session, run: CatalogingChapterRun) -> int | None:
    scenes = plan_scenes(db, run)
    return len(scenes) if scenes is not None else None


def validate_scene_submission(records: list[dict[str, Any]], scene_count: int | None) -> None:
    """One submitted section per declared scene; no repeated batch overwrites."""
    sections = [row for row in records if row.get("type") in {"outline_create", "outline_update"}
                and row.get("node_type") == "section"]
    if scene_count is not None and len(sections) > scene_count:
        raise ValueError(
            f"本次提交了 {len(sections)} 条场景小节，超过本章 {scene_count} 个场景；"
            "请按 chapter_summary.scenes 合并同场事件，每个场景只提交一条。"
        )
    numbers = [row.get("scene_number") for row in sections]
    valid_numbers = [number for number in numbers if isinstance(number, int)
                     and not isinstance(number, bool) and number > 0]
    if len(valid_numbers) != len(set(valid_numbers)):
        raise ValueError("同一批场景小节的 scene_number 重复；每个场景只提交一条。")


def active_scene_candidates(db: Session, run: CatalogingChapterRun) -> list[CatalogingCandidate]:
    return [row for row in db.query(CatalogingCandidate).filter(
        CatalogingCandidate.chapter_run_id == run.id,
        CatalogingCandidate.status != "rejected",
        CatalogingCandidate.item_type.in_(("outline_create", "outline_update")),
    ).order_by(CatalogingCandidate.sort_order, CatalogingCandidate.id).all()
        if normalize_node_type(candidate_payload(row).get("node_type")) == "section"]


def scene_repair_context(db: Session, run: CatalogingChapterRun) -> dict[str, Any]:
    scenes = plan_scenes(db, run)
    sections = active_scene_candidates(db, run)
    declared = None
    for row in db.query(CatalogingCandidate).filter_by(
        chapter_run_id=run.id, item_type="chapter_summary",
    ).filter(CatalogingCandidate.status != "rejected").all():
        manifest = candidate_payload(row).get("coverage_manifest")
        if isinstance(manifest, dict):
            declared = manifest.get("scene_count")
    count = len(scenes) if scenes else declared
    numbers = [candidate_payload(row).get("scene_number") for row in sections]
    invalid_numbers = any(not isinstance(number, int) or isinstance(number, bool)
                          or number <= 0 or (isinstance(count, int) and number > count)
                          for number in numbers)
    requires_replacement = invalid_numbers or len(numbers) != len(set(numbers))
    return {
        "source_scene_count": len(scenes) if scenes is not None else None,
        "source_scenes": [{"scene_number": i, "scene": scene}
                          for i, scene in enumerate(scenes or [], 1)],
        "declared_scene_count": declared,
        "required_repair_type": "scene_outline_replace" if requires_replacement else None,
        "expected_candidate_ids": [row.id for row in sections],
        "section_candidates": [{"candidate_id": row.id, "payload": candidate_payload(row)}
                               for row in sections],
    }


def validate_scene_candidate(
    db: Session, run: CatalogingChapterRun, normalized: dict[str, Any],
    *, replacing_plan: bool = False,
) -> list[CatalogingCandidate]:
    """Validate model-selected numbers and IDs; never infer a scene from text."""
    payload = normalized["payload"]
    item_type = normalized["item_type"]
    is_section = item_type in {"outline_create", "outline_update"} and (
        normalize_node_type(payload.get("node_type")) == "section"
    )
    if item_type == "chapter_summary":
        scenes = payload.get("scenes")
        manifest = payload.get("coverage_manifest")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("chapter_summary.scenes 必须是同一建档计划确定的非空场景数组")
        if not isinstance(manifest, dict) or manifest.get("scene_count") != len(scenes):
            raise ValueError("coverage_manifest.scene_count 必须等于本次计划 scenes 的长度")
    if not is_section:
        return []

    context = scene_repair_context(db, run)
    count = context["source_scene_count"] or context["declared_scene_count"]
    number = payload.get("scene_number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise ValueError("section.scene_number 必须由模型明确填写正整数")
    if isinstance(count, int) and count > 0 and number > count:
        raise ValueError(
            f"section.scene_number={number} 越界；本章场景范围为 1..{count}。"
            "按 chapter_summary.scenes 合并同场事件，不得将事件条数当场景数。"
            "该候选未保存；不要仅换编号覆盖已有场景，须核对完整计划场景和已保存候选。"
        )
    if not replacing_plan and isinstance(count, int) and count > 0:
        existing_numbers = [item["payload"].get("scene_number")
                            for item in context["section_candidates"]]
        if (any(not isinstance(value, int) or isinstance(value, bool)
                or value < 1 or value > count for value in existing_numbers)
                or len(existing_numbers) != len(set(existing_numbers))):
            raise ValueError(
                "旧检查点场景编号冲突；必须用 scene_outline_replace 完整重排全部场景，"
                "不能只改末条编号覆盖已有场景"
            )
    return []


def is_scene_replacement(raw: dict[str, Any]) -> bool:
    return (raw.get("type") or raw.get("item_type")) == "scene_outline_replace"


def validate_scene_replacement(db: Session, run: CatalogingChapterRun, raw: dict[str, Any]):
    from .records import normalize_candidate

    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
    expected = payload.get("expected_candidate_ids")
    sections = payload.get("sections")
    if (not isinstance(expected, list) or not expected
            or any(not isinstance(value, str) or not value for value in expected)
            or len(expected) != len(set(expected))):
        raise ValueError("expected_candidate_ids 必须列出当前全部场景候选的真实 ID，不可为空或重复")
    context = scene_repair_context(db, run)
    count = context["source_scene_count"] or context["declared_scene_count"]
    if not isinstance(count, int) or count <= 0:
        raise ValueError("场景重排需要已保存的计划场景或场景数声明")
    if not isinstance(sections, list) or len(sections) != count:
        raise ValueError(
            f"scene_outline_replace.sections 必须一次提交全部 {count} 个场景，不能只修最后一条"
        )
    numbers = []
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("sections 每项必须是完整 section 候选对象")
        normalized = normalize_candidate(section)
        body = normalized["payload"]
        if (normalized["item_type"] not in {"outline_create", "outline_update"}
                or normalize_node_type(body.get("node_type")) != "section"):
            raise ValueError("sections 只能包含 section 场景候选，不能夹带章级大纲或其他业务候选")
        validate_scene_candidate(db, run, normalized, replacing_plan=True)
        missing = [field for field in SECTION_SCENE_STATE_FIELDS if field not in body]
        if missing or not str(body.get("summary") or "").strip():
            raise ValueError(
                "每个替换场景必须包含完整 summary 和场景状态字段：" + ", ".join(missing)
            )
        numbers.append(body["scene_number"])
    if sorted(numbers) != list(range(1, count + 1)):
        raise ValueError(f"sections 必须完整且唯一覆盖 scene_number=1..{count}")
    targets = [db.get(CatalogingCandidate, identity) for identity in expected]
    for row in targets:
        if (row is None or row.chapter_run_id != run.id or row.job_id != run.job_id
                or row.project_id != run.project_id or row.chapter_id != run.chapter_id
                or row.item_type not in {"outline_create", "outline_update"}
                or normalize_node_type(candidate_payload(row).get("node_type")) != "section"):
            raise ValueError("expected_candidate_ids 必须属于当前作品、章节和运行中的场景候选")
    plan_hash = hashlib.sha256(json.dumps(
        sections, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    marker = "scene_plan:" + plan_hash + ":"
    active = active_scene_candidates(db, run)
    if all(row.status == "rejected" and str(row.error or "").startswith(marker) for row in targets):
        ids = set(str(targets[0].error)[len(marker):].split(","))
        if ids == {row.id for row in active} and len({row.error for row in targets}) == 1:
            return targets, sections, marker, True
    if {row.id for row in active} != set(expected):
        actual = {row.id for row in active}
        raise ValueError(
            "expected_candidate_ids 与当前完整场景候选集不一致；"
            f"缺少 ID={sorted(actual - set(expected))}；多余 ID={sorted(set(expected) - actual)}。"
            "逐项复制 scene_repair.expected_candidate_ids，必须包含待退役的越界候选 ID；"
            "旧候选 ID 数量可以多于新场景数量，不得只列 1..N 的候选"
        )
    if any(row.status != "pending" or row.edited_payload is not None for row in targets):
        raise ValueError("只能重排未编辑的 pending 场景候选，不能覆盖作者修改或已应用记录")
    return targets, sections, marker, False
