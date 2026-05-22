"""Conflict suggestion, character change detection, and worldbuilding conflict detection workspace tools."""
from __future__ import annotations

import json as _json
import re as _re
from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....database.models import (
    Chapter,
    ChapterCharacter,
    Character,
    CharacterChangeLog,
    CharacterRelationship,
    CharacterTimeline,
    Project,
    WorldbuildingEntry,
)
from ....services.context_builders import (
    _build_outline_context,
    _build_recent_summaries,
)


async def suggest_conflicts(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    prompt = str(args.get("prompt") or "").strip() or None

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "suggest_conflicts", "status": "skipped", "detail": "项目不存在", "data": []}

    outline_ctx = _build_outline_context(db, project_id, outline_node_id) if outline_node_id else "未限定大纲节点。"
    summaries = _build_recent_summaries(db, project_id, 5)

    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .order_by(Character.updated_at.desc())
        .limit(10)
        .all()
    )
    char_context = "\n".join(
        f"- {c.name}（{c.role_type or '未分类'}）: {(c.personality or '')[:200]}"
        for c in characters
    ) or "暂无角色。"

    relationships = (
        db.query(CharacterRelationship)
        .filter(CharacterRelationship.project_id == project_id)
        .limit(20)
        .all()
    )
    character_ids = {r.character_a_id for r in relationships} | {r.character_b_id for r in relationships}
    name_map = {
        c.id: c.name
        for c in db.query(Character).filter(Character.id.in_(character_ids)).all()
    }
    rel_context = "\n".join(
        f"- {name_map.get(r.character_a_id, r.character_a_id[:8])} ↔ {name_map.get(r.character_b_id, r.character_b_id[:8])}: {r.relationship_type}"
        for r in relationships
    ) or "暂无已知关系。"

    messages = [
        {
            "role": "system",
            "content": (
                "你是一位资深小说情节编辑，专精于戏剧冲突设计。你深谙「没有冲突就没有故事」的原则，能为任何剧情阶段注入恰到好处的张力。\n\n"
                "【任务】\n"
                "根据当前剧情状态，分析并设计3种不同类型的冲突方案，每种类型提供一个具体建议。\n\n"
                "【冲突类型定义】\n"
                "- personality（人物冲突）：角色之间的矛盾——目标对立、价值观碰撞、误解、背叛、竞争。此类型必须指定两个以上具体角色名。\n"
                "- faction（势力冲突）：组织或阵营之间的对抗——门派争斗、国家战争、阶级对立、资源争夺。此类型必须明确对立的双方。\n"
                "- inner（内心冲突）：角色内在的挣扎——道德困境、欲望与责任的拉扯、自我认同的危机、创伤后应激。此类型聚焦单一角色的心理层面。\n\n"
                "【设计原则】\n"
                "1. 每个冲突必须基于已有的角色、关系和世界观设定——不能凭空创造不存在的新势力或新人物。\n"
                "2. 每个冲突必须有清晰的起因（为什么现在爆发）、过程（冲突如何升级）和可行方向（如何解决或恶化）。\n"
                "3. tension_level（张力等级）的判断标准：low=可缓和的分歧、medium=需要做出选择的矛盾、high=不可调和的对抗。\n"
                "4. 冲突建议应具体可落地——详细描述冲突场景而非抽象概念。\n\n"
                "【禁止事项】\n"
                "- 禁止建议与已有剧情和角色设定矛盾或重复的冲突。\n"
                "- 禁止引入【角色列表】中不存在的角色。\n"
                "- 禁止输出JSON以外的任何内容。\n\n"
                "【输出格式】\n"
                "只输出JSON对象，格式：\n"
                '{"conflicts":[{"type":"personality|faction|inner","title":"","description":"","involved_characters":[""],"tension_level":"low|medium|high","suggested_outcome":""}]}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"作品：{project.title}\n"
                f"简介：{project.description or '暂无'}\n\n"
                f"【当前大纲】\n{outline_ctx}\n\n"
                f"【前文摘要】\n{summaries}\n\n"
                f"【角色列表】\n{char_context}\n\n"
                f"【已知关系】\n{rel_context}\n\n"
                f"{'用户倾向: ' + prompt if prompt else ''}\n\n"
                "请分析并提供3种情节冲突建议。"
            ),
        },
    ]

    model = str(args.get("model") or "") or None
    temperature = float(args.get("temperature") or 0.8)

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "suggest_conflicts", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": []}

    suggestion_text = result.get("content", "")
    parsed = None
    try:
        parsed = _json.loads(suggestion_text.strip().removeprefix("```json").removesuffix("```").strip())
    except _json.JSONDecodeError:
        parsed = None

    conflicts = parsed.get("conflicts", []) if parsed else []
    return {
        "tool": "suggest_conflicts",
        "status": "ok",
        "detail": f"已生成 {len(conflicts)} 条冲突建议",
        "data": {
            "conflicts": conflicts,
            "model": result.get("model"),
        },
    }


async def detect_character_changes(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter_id = str(args.get("chapter_id") or "").strip()
    if not chapter_id:
        return {"tool": "detect_character_changes", "status": "skipped", "detail": "缺少章节ID（chapter_id）", "data": []}

    chapter = db.query(Chapter).filter(Chapter.id == chapter_id, Chapter.project_id == project_id).first()
    if not chapter:
        return {"tool": "detect_character_changes", "status": "skipped", "detail": "章节不存在", "data": []}

    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id, Character.is_evolution_tracked == True)
        .all()
    )
    if not characters:
        return {"tool": "detect_character_changes", "status": "ok", "detail": "没有开启演化追踪的角色", "data": {"changes": [], "total": 0}}

    character_by_id = {c.id: c for c in characters}
    char_payload = [
        {
            "id": c.id,
            "name": c.name,
            "personality": c.personality,
            "abilities": c.abilities,
            "background": c.background,
            "role_type": c.role_type,
        }
        for c in characters
    ]

    chapter_text = chapter.content or ""
    if len(chapter_text) > 8000:
        chapter_text = chapter_text[:8000] + "\n...(后续内容已截断)"

    messages = [
        {
            "role": "system",
            "content": (
                "你是一位小说角色设定追踪编辑，专精于检测角色在剧情推进中发生的可记录变化。你理解角色弧光理论——角色应随着经历而成长、改变或恶化。\n\n"
                "【任务】\n"
                "分析新章节内容，对比当前角色档案，检测每个角色发生的所有可记录变化。\n\n"
                "【变化类型定义与判断标准】\n"
                "- skill（技能/能力变化）：角色习得新技能、失去旧能力、能力显著增强或减弱。判断标准：原文明确描写了学习/失去/变化的过程或结果。\n"
                "- experience（重要经历）：角色经历了改变其认知、地位或命运的重大事件。判断标准：该事件在原文中有明确的因果影响或情感冲击。\n"
                "- relationship（关系变化）：角色与他人的关系发生了实质性改变——从陌生到熟悉、从友好到敌对、从平等变为从属等。判断标准：原文中有关系状态转变的具体描写。\n"
                "- personality（性格成长）：角色的性格特征发生了可观察的演变——变得勇敢/懦弱、开朗/阴郁、果断/犹豫等。判断标准：角色的言行模式与旧档案描述有显著差异，且不是临时情绪反应。\n\n"
                "【检测精度要求】\n"
                "1. 区分永久变化与临时状态：角色因醉酒、被控制、极度恐惧等短暂状态下的行为改变不算性格变化。\n"
                "2. 区分显性变化与隐性变化：有些变化是角色自己意识到的（显性），有些是读者能感知但角色尚未意识到的（隐性）。两种都应检测。\n"
                "3. confidence 判断标准：\n"
                "   - high：原文有明确语句支持该变化（如「从那以后，他变得...」、「他终于学会了...」）\n"
                "   - medium：原文暗示了变化但未明说（多个场景表现出与旧档案不同的行为模式）\n"
                "   - low：仅有模糊迹象，可能只是暂时状态或解读偏差\n"
                "4. old_value 应从当前角色档案中提取对应字段的值，new_value 应从原文中提取具体描述。若旧档案中对应字段为空，old_value 填写「（档案中无记录）」。\n\n"
                "【禁止事项】\n"
                "- 禁止为没有发生变化的角色强行编造变化。无变化就输出空数组 []。\n"
                "- 禁止将临时情绪波动标记为性格变化。\n"
                "- 禁止将原文中未发生的事情标记为变化。\n"
                "- 禁止输出JSON数组以外的任何内容。\n\n"
                "【输出格式】\n"
                "只输出JSON数组：\n"
                '[{"character_id":"","character_name":"","change_type":"skill|experience|relationship|personality",'
                '"field_name":"","old_value":"","new_value":"","confidence":"high|medium|low"}]\n'
                "如果没有明显变化，输出 []。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"章节标题：{chapter.title}\n"
                f"章节内容：\n{chapter_text}\n\n"
                f"当前角色档案：\n{_json.dumps(char_payload, ensure_ascii=False)}"
            ),
        },
    ]

    model = str(args.get("model") or "") or None
    temperature = float(args.get("temperature") or 0.3)

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "detect_character_changes", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": []}

    changes_text = result.get("content", "")
    changes = []
    try:
        changes = _json.loads(changes_text.strip().removeprefix("```json").removesuffix("```").strip())
    except _json.JSONDecodeError:
        pass

    saved_changes = []
    allowed_change_types = {"skill", "experience", "relationship", "personality"}
    default_field_by_type = {
        "skill": "abilities",
        "experience": "background",
        "relationship": "background",
        "personality": "personality",
    }
    allowed_fields = {"abilities", "personality", "background", "appearance"}
    timeline_type_by_change = {
        "skill": "skill_gain",
        "experience": "key_decision",
        "relationship": "relationship_change",
        "personality": "emotional_turning_point",
    }

    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, dict):
                continue
            char_id = str(change.get("character_id", "")).strip()
            if char_id not in character_by_id:
                continue
            change_type = str(change.get("change_type", "experience")).strip()
            if change_type not in allowed_change_types:
                change_type = "experience"
            field_name = str(change.get("field_name") or default_field_by_type[change_type]).strip()
            if field_name not in allowed_fields:
                field_name = default_field_by_type[change_type]
            old_val = str(change.get("old_value", ""))[:2000] if change.get("old_value") else None
            new_val = str(change.get("new_value", ""))[:2000] if change.get("new_value") else None

            log = CharacterChangeLog(
                character_id=char_id,
                chapter_id=chapter_id,
                change_type=change_type,
                field_name=field_name,
                old_value=old_val,
                new_value=new_val,
                confirmed=False,
            )
            db.add(log)
            db.flush()
            confidence = str(change.get("confidence", "medium") or "medium")
            saved_changes.append({
                "id": log.id,
                "character_id": char_id,
                "character_name": character_by_id[char_id].name,
                "change_type": change_type,
                "field_name": field_name,
                "old_value": old_val,
                "new_value": new_val,
                "confidence": confidence,
            })

            char = character_by_id[char_id]
            existing_chapter_char = (
                db.query(ChapterCharacter)
                .filter(
                    ChapterCharacter.chapter_id == chapter_id,
                    ChapterCharacter.character_id == char_id,
                )
                .first()
            )
            if not existing_chapter_char:
                db.add(ChapterCharacter(
                    chapter_id=chapter_id,
                    character_id=char_id,
                    appearance_type="AI演化追踪",
                    description=f"检测到{change_type}变化，可信度：{confidence}",
                ))

            timeline_type = timeline_type_by_change.get(change_type, "key_decision")
            db.add(CharacterTimeline(
                character_id=char_id,
                chapter_id=chapter_id,
                event_type=timeline_type,
                event_description=f"[{change_type}] {field_name}: {new_val or '见原文'}",
                emotional_state_change=new_val if change_type == "personality" else None,
            ))

    db.commit()

    return {
        "tool": "detect_character_changes",
        "status": "ok",
        "detail": f"检测到 {len(saved_changes)} 处角色变化",
        "data": {
            "changes": saved_changes,
            "total": len(saved_changes),
        },
    }


DIMENSION_LABELS: dict[str, str] = {
    "geography": "地理",
    "history": "历史",
    "factions": "势力",
    "power_system": "规则体系",
    "races": "种族",
    "culture": "文化",
}


async def detect_worldbuilding_conflicts(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Detect logical contradictions between worldbuilding entries."""
    entries = (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id)
        .order_by(WorldbuildingEntry.dimension.asc(), WorldbuildingEntry.sort_order.asc())
        .all()
    )
    if len(entries) < 2:
        return {"tool": "detect_worldbuilding_conflicts", "status": "ok", "detail": "条目不足2个，无需检测矛盾", "data": {"conflicts": [], "total": 0}}

    entry_payload = [
        {
            "id": entry.id,
            "dimension": entry.dimension,
            "dimension_label": DIMENSION_LABELS.get(entry.dimension, entry.dimension),
            "title": entry.title,
            "content": entry.content,
        }
        for entry in entries
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "你是一位小说设定一致性审校专家，专精于检测世界观条目之间的逻辑矛盾、规则冲突和历史不一致。你的工作是像侦探一样逐条比对，而不是泛泛检查。\n\n"
                "【检测维度】\n"
                "- 逻辑矛盾：两个条目在因果或概念上互相冲突（如条目A说「灵气在千年前枯竭」，条目B说「五百年前的灵气大战改变了世界格局」）。\n"
                "- 时间线冲突：两个条目中的时间先后顺序或年代标注互相矛盾。\n"
                "- 规则冲突：两个条目对同一力量体系、魔法规则或世界法则给出了不同的描述。\n"
                "- 势力关系冲突：两个条目对同一势力之间的关系给出了矛盾的定义（如A说X和Y是同盟，B说X和Y是敌对）。\n"
                "- 种族文化冲突：两个条目对同一种族或文化的特征给出了不一致的描述。\n\n"
                "【严重度判断标准】\n"
                "- high：直接矛盾，无法通过任何合理方式调和，必须修改其中一个条目。\n"
                "- medium：存在不一致但可以通过添加条件或限定词调和。\n"
                "- low：措辞或细节上的细微差异，不影响整体逻辑。\n\n"
                "【输出格式】\n"
                "只输出JSON对象，不要输出解释、前缀或Markdown：\n"
                '{"conflicts":[{"entry_a_id":"","entry_b_id":"","dimension":"","severity":"low|medium|high","summary":"一句话摘要","detail":"具体矛盾说明"}]}\n'
                "如果没有发现矛盾，输出 {\"conflicts\": []}。不要强行编造不存在的矛盾。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请检查以下世界观条目之间是否存在逻辑矛盾、时间线冲突、"
                "规则冲突、势力关系冲突或种族文化冲突。\n"
                "返回 JSON 格式："
                '{"conflicts":[{"entry_a_id":"...","entry_b_id":"...",'
                '"dimension":"...","severity":"low|medium|high",'
                '"summary":"一句话摘要","detail":"具体矛盾说明"}]}\n\n'
                f"条目列表：\n{_json.dumps(entry_payload, ensure_ascii=False)}"
            ),
        },
    ]

    model = str(args.get("model") or "") or None
    temperature = float(args.get("temperature") or 0.2)

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "detect_worldbuilding_conflicts", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": []}

    analysis = result.get("content", "")
    valid_entry_ids = {entry.id for entry in entries}

    # Parse conflicts
    conflicts = []
    try:
        stripped = analysis.strip()
        fence_match = _re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=_re.DOTALL | _re.IGNORECASE)
        if fence_match:
            stripped = fence_match.group(1).strip()
        parsed = _json.loads(stripped)
        raw_conflicts = parsed.get("conflicts", parsed if isinstance(parsed, list) else [])
        if isinstance(raw_conflicts, list):
            for item in raw_conflicts:
                if not isinstance(item, dict):
                    continue
                entry_a = str(item.get("entry_a_id", ""))
                entry_b = str(item.get("entry_b_id", ""))
                if entry_a not in valid_entry_ids or entry_b not in valid_entry_ids:
                    continue
                conflicts.append({
                    "entry_a_id": entry_a,
                    "entry_b_id": entry_b,
                    "dimension": item.get("dimension", ""),
                    "severity": item.get("severity", "low"),
                    "summary": item.get("summary", ""),
                    "detail": item.get("detail", ""),
                })
    except (_json.JSONDecodeError, AttributeError):
        conflicts = []

    return {
        "tool": "detect_worldbuilding_conflicts",
        "status": "ok",
        "detail": f"发现 {len(conflicts)} 处设定矛盾" if conflicts else "暂未检测到明显设定矛盾",
        "data": {
            "conflicts": conflicts,
            "total": len(conflicts),
            "model": result.get("model"),
        },
    }
