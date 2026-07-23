"""Read-time compatibility projections for historical novel-creation drafts."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _looks_like_lifecycle_event(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    event_type = _text(data.get("type")).lower().replace("-", "_")
    part_type = _text(_record(data.get("part")).get("type")).lower().replace("-", "_")
    lifecycle_types = {"step_start", "step_finish", "message_start", "message_finish", "tool_start", "tool_finish"}
    return event_type in lifecycle_types or part_type in lifecycle_types


def project_legacy_draft(draft: dict[str, Any], stage_order: tuple[str, ...]) -> dict[str, Any]:
    projected = deepcopy(draft)
    stages = _record(projected.get("stages"))
    for stage in stage_order:
        state = _record(stages.get(stage))
        if _looks_like_lifecycle_event(state.get("data")):
            state.update({
                "status": "stale",
                "data": None,
                "stale_reason": "历史模型只返回了运行状态，请重新生成本阶段",
            })
            stages[stage] = state
    projected["stages"] = stages
    return projected


def projected_generation_blockers(
    draft: dict[str, Any],
    stage: str,
    stage_order: tuple[str, ...],
    stage_labels: dict[str, str],
) -> list[dict[str, str]]:
    if stage not in {*stage_order, "all"}:
        return [{"stage": stage, "label": stage, "reason": "unknown_stage"}]
    if stage in {"constraints", "concepts"}:
        return []
    stages = _record(draft.get("stages"))
    required = ("constraints", "concepts") if stage == "all" else stage_order[:stage_order.index(stage)]
    blockers = []
    for required_stage in required:
        status = _record(stages.get(required_stage)).get("status") or "pending"
        if status != "confirmed":
            blockers.append({
                "stage": required_stage,
                "label": stage_labels[required_stage],
                "reason": "stale" if status == "stale" else "not_confirmed",
            })
    return blockers
