"""Worldbuilding CRUD and AI expansion endpoints."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..ai.gateway import LLMGateway
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.models import Project, WorldbuildingEntry
from ..database.session import get_db
from ..schemas.worldbuilding import (
    WorldbuildingAIExpandRequest,
    WorldbuildingDimension,
    WorldbuildingEntryCreate,
    WorldbuildingEntryResponse,
    WorldbuildingEntryUpdate,
)

router = APIRouter(tags=["worldbuilding"])


DIMENSION_LABELS: dict[str, str] = {
    "geography": "地理",
    "history": "历史",
    "factions": "势力",
    "power_system": "规则体系",
    "races": "种族",
    "culture": "文化",
}


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")
    return project


def _get_entry_or_404(db: Session, project_id: str, entry_id: str) -> WorldbuildingEntry:
    entry = (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.id == entry_id, WorldbuildingEntry.project_id == project_id)
        .first()
    )
    if not entry:
        raise NotFoundError("世界观条目不存在")
    return entry


def _entry_to_dict(entry: WorldbuildingEntry) -> dict:
    return WorldbuildingEntryResponse.model_validate(entry).model_dump(mode="json")


def _group_entries(entries: list[WorldbuildingEntry], dimension: Optional[str] = None) -> dict:
    visible_dimensions = [dimension] if dimension else list(DIMENSION_LABELS.keys())
    grouped: dict[str, list[dict]] = {key: [] for key in visible_dimensions if key}

    for entry in entries:
        grouped.setdefault(entry.dimension, []).append(_entry_to_dict(entry))

    dimensions = [
        {
            "dimension": key,
            "label": DIMENSION_LABELS.get(key, key),
            "items": grouped.get(key, []),
        }
        for key in visible_dimensions
    ]
    return {
        "dimensions": dimensions,
        "grouped": grouped,
        "total": sum(len(items) for items in grouped.values()),
    }

@router.get("/projects/{project_id}/worldbuilding")
def list_worldbuilding_entries(
    project_id: str,
    dimension: Optional[WorldbuildingDimension] = Query(None, description="按维度过滤"),
    db: Session = Depends(get_db),
):
    """Get worldbuilding entries grouped by dimension."""
    _get_project_or_404(db, project_id)

    query = db.query(WorldbuildingEntry).filter(WorldbuildingEntry.project_id == project_id)
    if dimension:
        query = query.filter(WorldbuildingEntry.dimension == dimension)

    entries = (
        query.order_by(
            WorldbuildingEntry.dimension.asc(),
            WorldbuildingEntry.sort_order.asc(),
            WorldbuildingEntry.updated_at.desc(),
        )
        .all()
    )
    return ApiResponse.success(data=_group_entries(entries, dimension))


@router.post("/projects/{project_id}/worldbuilding")
def create_worldbuilding_entry(
    project_id: str,
    payload: WorldbuildingEntryCreate,
    db: Session = Depends(get_db),
):
    """Create a worldbuilding entry."""
    _get_project_or_404(db, project_id)

    entry = WorldbuildingEntry(project_id=project_id, **payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return ApiResponse.success(data=_entry_to_dict(entry), message="世界观条目创建成功")


@router.put("/projects/{project_id}/worldbuilding/{entry_id}")
def update_worldbuilding_entry(
    project_id: str,
    entry_id: str,
    payload: WorldbuildingEntryUpdate,
    db: Session = Depends(get_db),
):
    """Update a worldbuilding entry."""
    _get_project_or_404(db, project_id)
    entry = _get_entry_or_404(db, project_id, entry_id)

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise ValidationError("未提供任何更新字段")

    for field, value in update_data.items():
        setattr(entry, field, value)

    db.commit()
    db.refresh(entry)
    return ApiResponse.success(data=_entry_to_dict(entry), message="世界观条目更新成功")


@router.delete("/projects/{project_id}/worldbuilding/{entry_id}")
def delete_worldbuilding_entry(
    project_id: str,
    entry_id: str,
    db: Session = Depends(get_db),
):
    """Delete a worldbuilding entry."""
    _get_project_or_404(db, project_id)
    entry = _get_entry_or_404(db, project_id, entry_id)

    db.delete(entry)
    db.commit()
    return ApiResponse.success(message="世界观条目已删除")


@router.post("/projects/{project_id}/worldbuilding/ai-expand")
async def ai_expand_worldbuilding(
    project_id: str,
    payload: WorldbuildingAIExpandRequest,
    db: Session = Depends(get_db),
):
    """Generate a worldbuilding suggestion for a concept and dimension."""
    project = _get_project_or_404(db, project_id)
    existing_entries = (
        db.query(WorldbuildingEntry)
        .filter(
            WorldbuildingEntry.project_id == project_id,
            WorldbuildingEntry.dimension == payload.dimension,
        )
        .order_by(WorldbuildingEntry.sort_order.asc(), WorldbuildingEntry.updated_at.desc())
        .all()
    )
    existing_context = "\n".join(
        f"- {entry.title}: {entry.content[:600]}" for entry in existing_entries
    ) or "暂无既有设定。"

    dimension_label = DIMENSION_LABELS[payload.dimension]
    messages = [
        {
            "role": "system",
            "content": (
                "你是一位资深世界观架构师，专精于为小说作品构建逻辑自洽、细节丰富、可直接服务于剧情写作的设定体系。你深谙「好设定必有规则，好规则必有代价」的原则。\n\n"
                "【任务】\n"
                "根据作者提供的概念关键词和已有设定，为指定维度生成一段可插入的世界观条目正文。\n\n"
                "【设定必须包含】\n"
                "1. 起源/成因：该设定从何而来——历史渊源、自然演化或人为创造。\n"
                "2. 规则/运行机制：该设定如何运作——具体规则、触发条件和生效范围。\n"
                "3. 限制/弱点：该设定的边界在哪里——有什么代价、能被什么克制、在什么条件下失效。\n"
                "4. 可引发的剧情张力：该设定可以如何驱动故事——能产生什么样的冲突、困境或转折。\n\n"
                "【质量要求】\n"
                "- 与已有设定严格逻辑自洽，不得与现存条目矛盾。如果有潜在的关联点，明确指出。\n"
                "- 输出中文，包含具体可落地的细节——举例子比讲道理更有用。\n"
                "- 避免空泛描述（如「某地神秘莫测」——必须具体说明神秘在何处、有何迹象）。\n"
                "- 字数：120-600字。\n\n"
                "【禁止事项】\n"
                "- 禁止输出标题（条目标题由作者填入的维度+概念决定）。\n"
                "- 禁止使用 Markdown 表格或代码块。\n"
                "- 禁止写「这是一个关于」「本设定描述」等元描述句。\n"
                "- 禁止编造与已有内容矛盾的新规则而不做说明。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"作品标题：{project.title}\n"
                f"作品简介：{project.description or '暂无'}\n"
                f"目标维度：{dimension_label}（{payload.dimension}）\n"
                f"作者概念/关键词：{payload.concept}\n\n"
                f"当前同维度既有设定：\n{existing_context}\n\n"
                "请生成一段适合作为世界观条目正文的设定建议。"
                "要求：120-600字；包含起源/规则/限制/可引发的剧情张力；"
                "不要输出标题，不要使用 Markdown 表格。"
            ),
        },
    ]
    result = await LLMGateway.chat_completion(
        messages=messages,
        model=payload.model,
        temperature=0.7,
    )

    return ApiResponse.success(
        data={
            "dimension": payload.dimension,
            "dimension_label": dimension_label,
            "concept": payload.concept,
            "suggestion": result.get("content", ""),
            "model": result.get("model"),
            "usage": result.get("usage"),
        }
    )

