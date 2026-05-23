"""Plot design workspace tool."""
from __future__ import annotations

import json as _json
import re as _re
from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....database.models import (
    Character,
    CharacterRelationship,
    Chapter,
    ChapterCharacter,
    Project,
    WorldbuildingEntry,
)
from ....services.context_builders import (
    _build_outline_context,
    _build_outline_overview,
    _build_recent_summaries,
    _build_scene_characters_context,
    _build_world_context,
)
from ....services.style_rules import _build_style_context


async def design_plot(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    involved_names: list[str] = (
        [str(n).strip() for n in args.get("involved_characters", []) if n]
        if isinstance(args.get("involved_characters"), list)
        else []
    )
    requirements = str(args.get("requirements") or "").strip()
    feedback = str(args.get("feedback") or "").strip()
    previous_plot = args.get("previous_plot")  # For iteration

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "design_plot", "status": "skipped", "detail": "项目不存在", "data": {}}

    # Context: outline
    outline_ctx = _build_outline_context(db, project_id, outline_node_id) if outline_node_id else "无指定大纲节点。"
    outline_overview = _build_outline_overview(db, project_id, limit=40)

    # Context: worldbuilding
    world_ctx = _build_world_context(db, project_id, outline_node_id)

    # Context: recent summaries
    summaries = _build_recent_summaries(db, project_id, limit=5)

    # Context: scene characters
    scene_chars = _build_scene_characters_context(db, project_id, outline_node_id)

    # Context: style
    style_ctx = _build_style_context(project)

    # Context: involved characters detail
    char_details: list[str] = []
    if involved_names:
        characters = (
            db.query(Character)
            .filter(Character.project_id == project_id, Character.name.in_(involved_names))
            .all()
        )
        for c in characters:
            detail_parts = [
                f"【{c.name}】",
                f"  身份: {c.role_type or '未设定'}",
                f"  性格: {(c.personality or '未设定')[:300]}",
                f"  背景: {(c.background or '未设定')[:300]}",
                f"  能力: {(c.abilities or '未设定')[:200]}",
                f"  外貌: {(c.appearance or '未设定')[:150]}",
            ]
            # Relationships
            rels = (
                db.query(CharacterRelationship)
                .filter(
                    CharacterRelationship.project_id == project_id,
                    (CharacterRelationship.character_a_id == c.id)
                    | (CharacterRelationship.character_b_id == c.id),
                )
                .limit(10)
                .all()
            )
            if rels:
                all_char_ids = {r.character_a_id for r in rels} | {r.character_b_id for r in rels}
                name_map = {
                    ch.id: ch.name
                    for ch in db.query(Character).filter(Character.id.in_(all_char_ids)).all()
                }
                rel_lines = []
                for r in rels:
                    other = name_map.get(
                        r.character_b_id if r.character_a_id == c.id else r.character_a_id, "?"
                    )
                    rel_lines.append(f"    {other}: {r.relationship_type}")
                if rel_lines:
                    detail_parts.append(f"  关系:\n" + "\n".join(rel_lines))
            char_details.append("\n".join(detail_parts))
    char_detail_text = "\n\n".join(char_details) if char_details else "未指定角色。"

    # Existing chapters under this outline node (to avoid duplication)
    existing_chapters_text = "暂无已有章节。"
    if outline_node_id:
        existing = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.outline_node_id == outline_node_id)
            .order_by(Chapter.created_at.asc())
            .all()
        )
        if existing:
            existing_chapters_text = "\n".join(
                f"- [{ch.created_at.strftime('%m-%d')}] {ch.title}: {(ch.summary.summary_text if ch.summary else ch.content or '')[:200]}"
                for ch in existing
            )

    # Build previous plot feedback for iteration
    iteration_context = ""
    if feedback and previous_plot:
        iteration_context = (
            "【上一轮剧情设计】（需要修改）\n"
            f"{_json.dumps(previous_plot, ensure_ascii=False, indent=2)}\n\n"
            f"【修改意见】\n{feedback}\n\n"
            "请根据修改意见重新设计剧情。\n\n"
        )

    messages = [
        {
            "role": "system",
            "content": (
                "你是一位资深小说剧情设计师，专精于设计引人入胜、逻辑自洽的章节剧情。你设计的情节不是流水账——每一场戏都必须同时推动剧情、揭示角色或制造紧张。\n\n"
                "【任务】\n"
                "根据提供的大纲、角色、世界观和前文摘要，设计本章节的详细剧情。你的设计将被ReAct智能体审核，通过后才会交给角色扮演工具和写手工具去执行。\n\n"
                "【设计维度 — 必须逐项完成】\n"
                "1. 场景拆解（scenes）：将本章拆分为 3-5 个连续场景，每个场景包含：地点、时间、出场角色、核心事件、场景目标（这场戏完成了什么）。\n"
                "2. 角色行为设计（character_actions）：每个出场角色在本章中的关键动作和动机——他们想要什么？为此做了什么？结果如何？\n"
                "3. 冲突与张力（conflicts）：本章的核心矛盾——角色间的冲突、角色与环境的冲突、或角色内心的冲突。描述冲突如何升级或转折。\n"
                "4. 情绪曲线（emotional_arc）：本章的情绪走向——从哪里开始（如平静/紧张/悲伤），经历什么转折，在哪里结束。标注情绪转折的关键事件。\n"
                "5. 设定一致性检查（consistency_check）：逐项核对——是否与已有大纲冲突？是否违反世界观规则？角色行为是否符合其性格和动机？是否有时间线矛盾？\n"
                "6. 新角色需求（new_characters_needed）：本章是否引入了新角色？如有，列出角色名、身份、出现原因、核心特征。如无，说明为什么现有角色已足够。\n"
                "7. 吸引力评估（engagement_assessment）：本章的看点是什么（悬念/反转/情感冲击/智斗/动作场面等）？读者为什么想要继续读下去？如果觉得不够，提出强化建议。\n\n"
                "【设计原则】\n"
                "- 每一个场景都必须回答'这段戏推动或改变或揭示了什么'。\n"
                "- 角色的每个行为必须有动机支撑——不要为了剧情需要而让角色做不符合性格的事。\n"
                "- 冲突必须具体、可感知——读者不需要通过分析来意识到'这里应该很紧张'。\n"
                "- 如果上一轮设计被指出问题，本轮必须针对性地修正，而不是在原方案上微调措辞。\n"
                "- 不要设计装饰性场景（如'角色A在花园散步思考'）——除非散步的内容推动剧情。\n\n"
                "【禁止事项】\n"
                "- 禁止输出泛泛的'本章围绕XX展开'式概括。每个场景都要有具体的动作和对话方向。\n"
                "- 禁止忽略已有章节——如果大纲节点下已有章节，新剧情必须承接上文。\n"
                "- 禁止设计超出当前大纲节点范围的内容。\n"
                "- 禁止凭空创造世界观中不存在的设定。\n"
                "- 禁止输出JSON以外的任何内容。\n\n"
                "【输出格式】\n"
                '{"scenes":[{"location":"","time":"","characters":[""],"core_event":"","goal":"","dialogue_direction":"该场景的对话方向或关键对话主题"}],'
                '"character_actions":[{"character_name":"","motivation":"","action":"","outcome":""}],'
                '"conflicts":{"type":"character|environment|inner","description":"","escalation":"冲突如何升级或转折","stakes":"如果处理不好会怎样"},'
                '"emotional_arc":{"start":"","turning_points":[{"event":"","emotion_shift":""}],"end":""},'
                '"consistency_check":{"outline_alignment":"","worldbuilding_compliance":"","character_consistency":"","timeline_check":"","potential_issues":""},'
                '"new_characters_needed":[{"name":"","identity":"","reason":"","core_traits":"","suggested_actor":"如果需要角色扮演，建议选哪个已有角色与之对话"}],'
                '"engagement_assessment":{"hooks":[""],"reader_appeal":"","strengthening_suggestions":""},'
                '"summary":"本章剧情一句话总结"}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"作品：{project.title}\n"
                f"简介：{project.description or '暂无'}\n\n"
                f"【完整大纲树】\n{outline_overview}\n\n"
                f"【当前大纲节点】\n{outline_ctx}\n\n"
                f"【世界观设定】\n{world_ctx}\n\n"
                f"【前文摘要】\n{summaries}\n\n"
                f"【该大纲下已有章节】\n{existing_chapters_text}\n\n"
                f"【场景已有角色】\n{scene_chars}\n\n"
                f"【本章涉及角色详情】\n{char_detail_text}\n\n"
                f"【作品文风约束】\n{style_ctx}\n\n"
                f"{'【用户要求】' + requirements if requirements else ''}\n\n"
                f"{iteration_context}"
                f"请为大纲节点设计本章的详细剧情。"
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
        return {"tool": "design_plot", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": {}}

    plot_text = result.get("content", "")
    parsed = None
    try:
        cleaned = plot_text.strip()
        fence_match = _re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=_re.DOTALL | _re.IGNORECASE)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        parsed = _json.loads(cleaned)
    except (_json.JSONDecodeError, AttributeError):
        parsed = None

    if not parsed or not isinstance(parsed, dict):
        return {
            "tool": "design_plot",
            "status": "error",
            "detail": "LLM 返回的剧情设计无法解析为JSON",
            "data": {"raw": plot_text[:2000]},
        }

    scenes = parsed.get("scenes", [])
    new_chars = parsed.get("new_characters_needed", [])
    issues = parsed.get("consistency_check", {}).get("potential_issues", "")

    return {
        "tool": "design_plot",
        "status": "ok",
        "detail": f"剧情设计完成：{len(scenes)} 个场景"
            + (f"，建议 {len(new_chars)} 个新角色" if new_chars else "")
            + (f"，发现潜在问题" if issues else ""),
        "data": parsed,
    }
