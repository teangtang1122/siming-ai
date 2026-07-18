"""Skill business logic — CRUD, scoring, prompt building, validation, built-in seeding."""
from __future__ import annotations

from app.architecture.uow import commit_session

import json
import logging
import re
from types import SimpleNamespace
from typing import Any
from typing import Optional

from sqlalchemy.orm import Session

from ...database.models import Skill, SkillVersion
from ...schemas.skill import SkillResponse
from .tool_catalog import get_tool_catalog

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

MAX_SINGLE_SKILL_PROMPT_CHARS = 1500
MAX_TOTAL_SKILL_PROMPT_CHARS = 4000
MAX_SKILLS_PER_REQUEST = 3
MIN_SCORE_THRESHOLD = 4

VALID_SCOPES = {"global", "project", "writing", "outline", "characters", "worldbuilding", "cataloging", "research"}

SKILL_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "writing_style",
        "name": "写作风格控制",
        "description": "约束章节生成时的语言风格、节奏、禁用句式和叙事偏好。",
        "scope": "writing",
        "trigger_examples": ["写作风格", "续写", "改写", "章节正文"],
        "recommended_tools": ["chapter_writer", "rewrite_text", "detect_forbidden_patterns"],
        "system_prompt": (
            "你正在执行写作风格控制技能。\n"
            "目标：{requirements}\n"
            "要求：\n"
            "1. 在生成或改写正文时严格遵守上述风格目标。\n"
            "2. 优先保持前文叙事连续、角色口吻一致，不为了风格牺牲剧情逻辑。\n"
            "3. 如果目标包含禁用句式、禁用词、少用比喻等约束，必须在最终文本中主动规避。\n"
            "4. 如需修改已有文本，先判断问题，再给出修改结果。"
        ),
    },
    {
        "key": "continuity_guard",
        "name": "连续性与设定检查",
        "description": "检查剧情、角色状态、世界观设定和前后章节是否冲突。",
        "scope": "writing",
        "trigger_examples": ["检查设定", "有没有矛盾", "是否合理", "剧情冲突"],
        "recommended_tools": [
            "preview_writing_context",
            "search_chapters",
            "search_characters",
            "search_worldbuilding",
            "detect_worldbuilding_conflicts",
        ],
        "system_prompt": (
            "你正在执行连续性与设定检查技能。\n"
            "目标：{requirements}\n"
            "要求：\n"
            "1. 先读取相关章节摘要、角色状态、世界观和大纲，不要凭空判断。\n"
            "2. 明确指出冲突位置、冲突原因、影响范围和修正建议。\n"
            "3. 如果没有证据证明冲突，不要过度推断。\n"
            "4. 输出应优先帮助用户修正文稿或规划后续剧情。"
        ),
    },
    {
        "key": "character_voice",
        "name": "角色口吻与扮演",
        "description": "强化角色说话方式、心理状态、行动动机和对戏一致性。",
        "scope": "characters",
        "trigger_examples": ["角色扮演", "角色口吻", "对话", "独白"],
        "recommended_tools": ["search_characters", "roleplay_character", "dialogue_battle"],
        "system_prompt": (
            "你正在执行角色口吻与扮演技能。\n"
            "目标：{requirements}\n"
            "要求：\n"
            "1. 先读取角色档案、当前状态、关系和出场记录。\n"
            "2. 角色行动必须符合其目标、冲突、身体状态和心理状态。\n"
            "3. 对话要体现角色差异，不要让所有角色使用同一种表达方式。\n"
            "4. 如果角色状态资料不足，先指出缺口，再谨慎生成。"
        ),
    },
    {
        "key": "research_digest",
        "name": "资料搜索与整理",
        "description": "搜索、整理、归纳外部或项目内资料，并可保存成记忆。",
        "scope": "research",
        "trigger_examples": ["搜索资料", "整理资料", "查一下", "收集信息"],
        "recommended_tools": ["web_search", "search_context", "remember", "list_memories"],
        "system_prompt": (
            "你正在执行资料搜索与整理技能。\n"
            "目标：{requirements}\n"
            "要求：\n"
            "1. 区分外部资料、项目内资料和用户偏好。\n"
            "2. 给出来源、摘要、可用场景和待验证点。\n"
            "3. 对长期有用的信息，主动建议保存为记忆。\n"
            "4. 不把未经确认的资料写成项目设定。"
        ),
    },
]

# Direct scope mapping: assistant scope → matching skill scopes
SCOPE_MAP: dict[str, set[str]] = {
    "outline": {"outline", "global"},
    "characters": {"characters", "global"},
    "worldbuilding": {"worldbuilding", "global"},
    "project": {"project", "global"},
}

# Intent keywords for scope override when payload.scope=project
INTENT_SCOPE_KEYWORDS: dict[str, list[str]] = {
    "writing": ["写", "续写", "章节", "正文", "对白", "段落", "改写", "扩写"],
    "outline": ["大纲", "剧情", "结构", "走向", "规划"],
    "characters": ["角色", "人物", "性格", "主角", "配角", "反派"],
    "worldbuilding": ["世界观", "设定", "修炼", "势力", "功法", "地图"],
    "research": ["搜索", "查找", "整理", "查一下", "找一下"],
    "cataloging": ["建档", "归档", "梳理"],
}

# Synonym expansion for trigger matching
TRIGGER_SYNONYMS: dict[str, list[str]] = {
    "续写": ["继续写", "接着写", "往下写", "写下去"],
    "审校": ["审阅", "校对", "检查"],
    "角色扮演": ["cosplay", "代入角色"],
    "大纲": ["目录", "框架"],
}

# Dangerous prompt patterns (combined verb + object)
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"(忽略|覆盖|绕过|无视|突破)", r"(系统指令|工具规则|安全规则|限制)"),
    (r"(泄露|显示|打印|输出|暴露)", r"(API\s*Key|密钥|token|密码|secret)"),
    (r"(删除|清空|格式化|销毁)", r"(全部数据|数据库|所有文件|所有数据)"),
    (r"(ignore|override|bypass|circumvent)", r"(system|instructions|rules|constraints)"),
    (r"(reveal|print|expose|leak)", r"(API\s*key|secret|token|password)"),
]


# ── JSON Helpers ───────────────────────────────────────────────────────

def parse_json_list(value: str | None) -> list[str]:
    """Parse a JSON array from text. Returns [] on non-array or error. Filters non-string items."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, str)]
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def dump_json_list(items: list[str] | None) -> str | None:
    """Serialize a list of strings to JSON. None → None."""
    if items is None:
        return None
    return json.dumps(items, ensure_ascii=False)


# ── Serialization ──────────────────────────────────────────────────────

def skill_to_dict(skill: Skill) -> dict:
    """Convert a Skill ORM instance to a response dict."""
    return SkillResponse(
        id=skill.id,
        project_id=skill.project_id,
        builtin_key=skill.builtin_key,
        name=skill.name,
        description=skill.description,
        trigger_examples=parse_json_list(skill.trigger_examples),
        system_prompt=skill.system_prompt,
        recommended_tools=parse_json_list(skill.recommended_tools),
        forbidden_tools=parse_json_list(skill.forbidden_tools),
        scope=skill.scope or "global",
        priority=skill.priority or 0,
        enabled=skill.enabled if skill.enabled is not None else True,
        is_builtin=skill.is_builtin if skill.is_builtin is not None else False,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    ).model_dump(mode="json")


def _snapshot_skill(skill: Skill) -> dict:
    """Return a durable, JSON-serializable snapshot of a skill."""
    return {
        "id": skill.id,
        "project_id": skill.project_id,
        "builtin_key": skill.builtin_key,
        "name": skill.name,
        "description": skill.description,
        "trigger_examples": parse_json_list(skill.trigger_examples),
        "system_prompt": skill.system_prompt,
        "recommended_tools": parse_json_list(skill.recommended_tools),
        "forbidden_tools": parse_json_list(skill.forbidden_tools),
        "scope": skill.scope or "global",
        "priority": skill.priority or 0,
        "enabled": bool(skill.enabled),
        "is_builtin": bool(skill.is_builtin),
    }


def _version_to_dict(version: SkillVersion) -> dict:
    try:
        snapshot = json.loads(version.snapshot_json or "{}")
    except (json.JSONDecodeError, TypeError):
        snapshot = {}
    return {
        "id": version.id,
        "skill_id": version.skill_id,
        "project_id": version.project_id,
        "title": version.title,
        "change_summary": version.change_summary,
        "snapshot": snapshot,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


def record_skill_version(
    db: Session,
    skill: Skill,
    *,
    title: str,
    change_summary: str | None = None,
) -> None:
    """Persist the current skill state as a version snapshot."""
    db.add(SkillVersion(
        skill_id=skill.id,
        project_id=skill.project_id,
        title=title[:200],
        change_summary=change_summary,
        snapshot_json=json.dumps(_snapshot_skill(skill), ensure_ascii=False),
    ))


def list_skill_versions(db: Session, project_id: str, skill_id: str) -> list[dict]:
    """Return version history for a skill, newest first."""
    get_skill_or_404(db, project_id, skill_id)
    versions = (
        db.query(SkillVersion)
        .filter(SkillVersion.project_id == project_id, SkillVersion.skill_id == skill_id)
        .order_by(SkillVersion.created_at.desc())
        .all()
    )
    return [_version_to_dict(v) for v in versions]


def list_skill_templates() -> list[dict]:
    """Return reusable templates for assisted skill creation."""
    return [dict(t) for t in SKILL_TEMPLATES]


def list_skill_tools() -> list[dict]:
    """Return workspace tool metadata for the skill editor."""
    return sorted(get_tool_catalog(), key=lambda item: str(item.get("name") or ""))


# ── Prompt Injection Protection ────────────────────────────────────────

def validate_skill_prompt(prompt: str) -> None:
    """Validate a custom skill prompt for dangerous patterns.

    Uses combined verb+object matching to avoid false positives.
    Raises ValidationError if a dangerous pattern is detected.
    """
    from ...core.exceptions import ValidationError

    prompt_lower = prompt.lower()
    for verb_pat, obj_pat in _DANGEROUS_PATTERNS:
        if re.search(verb_pat, prompt_lower, re.IGNORECASE) and re.search(obj_pat, prompt_lower, re.IGNORECASE):
            raise ValidationError(
                f"技能提示词包含潜在危险指令（检测到 {verb_pat} + {obj_pat} 模式），请修改后重试。"
            )


# ── CRUD ───────────────────────────────────────────────────────────────

def _extract_trigger_examples(text: str, template: dict | None = None) -> list[str]:
    """Extract a small deterministic trigger list from free-form requirements."""
    triggers: list[str] = []
    for item in (template or {}).get("trigger_examples", [])[:3]:
        if item and item not in triggers:
            triggers.append(item)

    for term in re.findall(r"[\u4e00-\u9fff]{2,10}|[A-Za-z][A-Za-z0-9_-]{2,30}", text or ""):
        if term not in triggers:
            triggers.append(term)
        if len(triggers) >= 8:
            break
    return triggers


def _select_template(template_key: str | None, scope: str, requirements: str) -> dict:
    if template_key:
        for template in SKILL_TEMPLATES:
            if template["key"] == template_key:
                return template

    text = f"{scope}\n{requirements}"
    for template in SKILL_TEMPLATES:
        if template["scope"] == scope:
            return template
    if any(word in text for word in ("角色", "口吻", "对话", "独白")):
        return next(t for t in SKILL_TEMPLATES if t["key"] == "character_voice")
    if any(word in text for word in ("搜索", "资料", "收集", "联网")):
        return next(t for t in SKILL_TEMPLATES if t["key"] == "research_digest")
    if any(word in text for word in ("矛盾", "检查", "设定", "合理")):
        return next(t for t in SKILL_TEMPLATES if t["key"] == "continuity_guard")
    return next(t for t in SKILL_TEMPLATES if t["key"] == "writing_style")


def build_skill_draft(requirements: str, template_key: str | None = None, scope: str = "global") -> dict:
    """Build an editable skill draft from user intent and a template."""
    from ...core.exceptions import ValidationError

    requirements = (requirements or "").strip()
    if not requirements:
        raise ValidationError("请输入技能需求")
    if scope not in VALID_SCOPES:
        raise ValidationError(f"无效的 scope 值: {scope}")

    template = _select_template(template_key, scope, requirements)
    draft_scope = scope if scope != "global" else template["scope"]
    safe_req = requirements[:1200]
    name_seed = re.sub(r"\s+", " ", requirements).strip("，。,. ")
    name = name_seed[:20] if name_seed else template["name"]
    if not name.endswith("技能"):
        name = f"{name}技能"

    draft = {
        "name": name,
        "description": f"根据需求自动生成：{requirements[:120]}",
        "trigger_examples": _extract_trigger_examples(requirements, template),
        "system_prompt": template["system_prompt"].format(requirements=safe_req),
        "recommended_tools": list(template["recommended_tools"]),
        "scope": draft_scope,
        "priority": 50,
        "enabled": True,
        "template_key": template["key"],
        "template_name": template["name"],
    }
    validate_skill_prompt(draft["system_prompt"])
    return draft


def preview_skill_match(
    db: Session,
    project_id: str,
    *,
    message: str,
    scope: str = "project",
    candidate: dict | None = None,
) -> dict:
    """Preview which skills would be injected for a user message."""
    matched = select_relevant_skills(db, project_id, message, scope)
    candidate_score = None
    candidate_included = False

    if candidate:
        fake_skill = SimpleNamespace(
            name=candidate.get("name") or "",
            description=candidate.get("description") or "",
            trigger_examples=dump_json_list(candidate.get("trigger_examples") or []),
            scope=candidate.get("scope") or "global",
        )
        candidate_score = _compute_skill_score(
            fake_skill, message, _resolve_matching_scopes(scope, message)
        )
        candidate_included = candidate_score >= MIN_SCORE_THRESHOLD

    section, info = build_skill_prompt_section(matched)
    # Attach per-skill prompt fragment to each matched skill
    for skill_dict, info_item in zip(matched, info):
        skill_dict["_prompt_fragment"] = info_item.get("prompt_fragment", "")
    return {
        "matched_skills": matched,
        "skill_prompt_preview": section,
        "skill_prompt_info": info,
        "candidate_score": candidate_score,
        "candidate_would_match": candidate_included,
        "threshold": MIN_SCORE_THRESHOLD,
        "max_skills": MAX_SKILLS_PER_REQUEST,
    }


def list_skills(db: Session, project_id: str) -> list[dict]:
    """List all skills for a project, seeding built-ins if none exist."""
    ensure_builtin_skills(db, project_id)
    skills = (
        db.query(Skill)
        .filter(Skill.project_id == project_id)
        .order_by(Skill.priority.desc(), Skill.created_at.asc())
        .all()
    )
    return [skill_to_dict(s) for s in skills]


def get_skill_or_404(db: Session, project_id: str, skill_id: str) -> Skill:
    from ...core.exceptions import NotFoundError
    skill = (
        db.query(Skill)
        .filter(Skill.id == skill_id, Skill.project_id == project_id)
        .first()
    )
    if not skill:
        raise NotFoundError("技能不存在")
    return skill


def create_skill(db: Session, project_id: str, data: dict) -> dict:
    from ...core.exceptions import ValidationError

    # Validate custom skill prompt
    validate_skill_prompt(data["system_prompt"])

    scope = data.get("scope", "global")
    if scope not in VALID_SCOPES:
        raise ValidationError(f"无效的 scope 值: {scope}，有效值: {', '.join(sorted(VALID_SCOPES))}")

    skill = Skill(
        project_id=project_id,
        name=data["name"],
        description=data.get("description"),
        trigger_examples=dump_json_list(data.get("trigger_examples", [])),
        system_prompt=data["system_prompt"],
        recommended_tools=dump_json_list(data.get("recommended_tools", [])),
        forbidden_tools=dump_json_list(data.get("forbidden_tools", [])),
        scope=scope,
        priority=data.get("priority", 0),
        enabled=data.get("enabled", True),
        is_builtin=False,
    )
    db.add(skill)
    try:
        commit_session(db)
    except Exception:
        db.rollback()
        raise ValidationError("同名技能已存在，请使用不同的名称")
    db.refresh(skill)
    record_skill_version(
        db,
        skill,
        title="创建技能",
        change_summary="初始技能配置",
    )
    commit_session(db)
    return skill_to_dict(skill)


def update_skill(db: Session, project_id: str, skill_id: str, data: dict) -> dict:
    from ...core.exceptions import ValidationError

    skill = get_skill_or_404(db, project_id, skill_id)
    before_snapshot = _snapshot_skill(skill)

    if "name" in data and data["name"] is not None:
        skill.name = data["name"]
    if "description" in data:
        skill.description = data["description"]
    if "trigger_examples" in data and data["trigger_examples"] is not None:
        skill.trigger_examples = dump_json_list(data["trigger_examples"])
    if "system_prompt" in data and data["system_prompt"] is not None:
        # Skip validation for built-in skills
        if not skill.is_builtin:
            validate_skill_prompt(data["system_prompt"])
        skill.system_prompt = data["system_prompt"]
    if "recommended_tools" in data and data["recommended_tools"] is not None:
        skill.recommended_tools = dump_json_list(data["recommended_tools"])
    if "forbidden_tools" in data and data["forbidden_tools"] is not None:
        skill.forbidden_tools = dump_json_list(data["forbidden_tools"])
    if "scope" in data and data["scope"] is not None:
        if data["scope"] not in VALID_SCOPES:
            raise ValidationError(f"无效的 scope 值: {data['scope']}，有效值: {', '.join(sorted(VALID_SCOPES))}")
        skill.scope = data["scope"]
    if "priority" in data and data["priority"] is not None:
        skill.priority = data["priority"]
    if "enabled" in data and data["enabled"] is not None:
        skill.enabled = data["enabled"]

    commit_session(db)
    db.refresh(skill)
    after_snapshot = _snapshot_skill(skill)
    changed_fields = [
        label
        for key, label in (
            ("name", "名称"),
            ("description", "描述"),
            ("trigger_examples", "触发示例"),
            ("system_prompt", "系统提示词"),
            ("recommended_tools", "推荐工具"),
            ("forbidden_tools", "禁用工具"),
            ("scope", "适用范围"),
            ("priority", "优先级"),
            ("enabled", "启用状态"),
        )
        if before_snapshot.get(key) != after_snapshot.get(key)
    ]
    if changed_fields:
        record_skill_version(
            db,
            skill,
            title="更新技能：" + "、".join(changed_fields[:4]),
            change_summary="变更字段：" + "、".join(changed_fields),
        )
        commit_session(db)
    return skill_to_dict(skill)


def delete_skill(db: Session, project_id: str, skill_id: str) -> None:
    from ...core.exceptions import ValidationError
    skill = get_skill_or_404(db, project_id, skill_id)
    if skill.is_builtin:
        raise ValidationError("内置技能不可删除，只能禁用")
    db.delete(skill)
    commit_session(db)


def reset_skill_to_builtin(db: Session, project_id: str, skill_id: str) -> dict:
    """Reset a built-in skill's fields back to the original BUILTIN_SKILLS values."""
    from ...core.exceptions import ValidationError

    skill = get_skill_or_404(db, project_id, skill_id)
    if not skill.is_builtin:
        raise ValidationError("只有内置技能可以恢复默认值")
    if not skill.builtin_key:
        raise ValidationError("该技能缺少 builtin_key，无法恢复默认值")

    builtin = next(
        (b for b in BUILTIN_SKILLS if b["builtin_key"] == skill.builtin_key),
        None,
    )
    if not builtin:
        raise ValidationError(f"未找到内置技能定义: {skill.builtin_key}")

    before_snapshot = _snapshot_skill(skill)

    skill.name = builtin["name"]
    skill.description = builtin["description"]
    skill.system_prompt = builtin["system_prompt"]
    skill.trigger_examples = json.dumps(builtin["trigger_examples"], ensure_ascii=False)
    skill.recommended_tools = json.dumps(builtin["recommended_tools"], ensure_ascii=False)
    skill.scope = builtin["scope"]
    skill.priority = builtin["priority"]

    commit_session(db)
    db.refresh(skill)

    after_snapshot = _snapshot_skill(skill)
    changed_fields = [
        label
        for key, label in (
            ("name", "名称"),
            ("description", "描述"),
            ("trigger_examples", "触发示例"),
            ("system_prompt", "系统提示词"),
            ("recommended_tools", "推荐工具"),
            ("scope", "适用范围"),
            ("priority", "优先级"),
        )
        if before_snapshot.get(key) != after_snapshot.get(key)
    ]
    if changed_fields:
        record_skill_version(
            db,
            skill,
            title="恢复内置技能默认值",
            change_summary="重置字段：" + "、".join(changed_fields),
        )
        commit_session(db)

    return skill_to_dict(skill)


# ── Skill Selection ────────────────────────────────────────────────────

def _resolve_matching_scopes(assistant_scope: str, user_message: str) -> set[str]:
    """Resolve which skill scopes match the current context.

    Combines direct scope mapping with intent-based override from user message.
    """
    scopes = set(SCOPE_MAP.get(assistant_scope, {"global"}))

    # Intent-based scope override when assistant scope is "project"
    if assistant_scope == "project":
        msg_lower = user_message.lower()
        for skill_scope, keywords in INTENT_SCOPE_KEYWORDS.items():
            for kw in keywords:
                if kw in msg_lower:
                    scopes.add(skill_scope)
                    break

    return scopes


def _compute_skill_score(
    skill: Skill,
    user_message: str,
    matching_scopes: set[str],
) -> int:
    """Compute a relevance score for a skill against the user message.

    Scoring:
      - name substring hit: +5
      - trigger_examples substring hit: +4
      - description keyword hit: +2
      - synonym hit: +2
      - scope match: +2
    """
    score = 0
    msg_lower = user_message.lower()

    # Name substring
    if skill.name and skill.name.lower() in msg_lower:
        score += 5

    # Trigger examples
    examples = parse_json_list(skill.trigger_examples)
    for ex in examples:
        if ex.lower() in msg_lower:
            score += 4
            break

    # Description keywords (split by common delimiters, check each word)
    if skill.description:
        desc_words = re.split(r"[，。、；\s,;]+", skill.description)
        for word in desc_words:
            if len(word) >= 2 and word.lower() in msg_lower:
                score += 2
                break

    # Synonym expansion
    for trigger_word, synonyms in TRIGGER_SYNONYMS.items():
        if trigger_word.lower() in msg_lower:
            for syn in synonyms:
                if syn.lower() in msg_lower:
                    score += 2
                    break
            break

    # Scope match
    skill_scope = skill.scope or "global"
    if skill_scope in matching_scopes:
        score += 2

    return score


def select_relevant_skills(
    db: Session,
    project_id: str,
    user_message: str,
    assistant_scope: str = "project",
) -> list[dict]:
    """Select enabled skills via deterministic scoring.

    Returns at most MAX_SKILLS_PER_REQUEST skills, sorted by
    priority DESC then score DESC. Each returned dict includes
    the computed score.
    """
    ensure_builtin_skills(db, project_id)

    skills = (
        db.query(Skill)
        .filter(Skill.project_id == project_id, Skill.enabled == True)
        .order_by(Skill.priority.desc())
        .all()
    )

    matching_scopes = _resolve_matching_scopes(assistant_scope, user_message)

    scored: list[tuple[Skill, int]] = []
    for skill in skills:
        score = _compute_skill_score(skill, user_message, matching_scopes)
        if score >= MIN_SCORE_THRESHOLD:
            scored.append((skill, score))

    # Sort by priority DESC, score DESC
    scored.sort(key=lambda x: (x[0].priority or 0, x[1]), reverse=True)

    # Take top N
    result = []
    for skill, score in scored[:MAX_SKILLS_PER_REQUEST]:
        d = skill_to_dict(skill)
        d["_score"] = score
        result.append(d)

    return result


# ── Prompt Building ────────────────────────────────────────────────────

def _build_tool_recommendation_line(skill: dict) -> str:
    """Build a tool recommendation/forbid line for a skill prompt."""
    recommended = skill.get("recommended_tools") or []
    forbidden = skill.get("forbidden_tools") or []
    parts: list[str] = []
    if recommended:
        parts.append(f"推荐工具：{', '.join(recommended)}")
    if forbidden:
        parts.append(f"禁用工具：{', '.join(forbidden)}")
    return "\n".join(parts)


def build_skill_prompt_section(matched_skills: list[dict]) -> tuple[str, list[dict]]:
    """Build the skill prompt section to inject into the system prompt.

    Returns (prompt_text, skill_info_list) where skill_info_list contains
    name, description, truncated, warnings, recommended_tools,
    prompt_fragment for each skill.

    Enforces:
      - Single skill prompt: MAX_SINGLE_SKILL_PROMPT_CHARS
      - Total section: MAX_TOTAL_SKILL_PROMPT_CHARS
      - Max skills: MAX_SKILLS_PER_REQUEST
    """
    if not matched_skills:
        return "", []

    sections: list[str] = []
    skill_info: list[dict] = []
    total_chars = 0

    for skill in matched_skills[:MAX_SKILLS_PER_REQUEST]:
        name = skill["name"]
        prompt = skill["system_prompt"]
        truncated = False
        warnings: list[str] = []

        # Build tool recommendation line
        tool_line = _build_tool_recommendation_line(skill)
        if tool_line:
            prompt = f"{prompt}\n{tool_line}"

        # Truncate single skill prompt if needed
        if len(prompt) > MAX_SINGLE_SKILL_PROMPT_CHARS:
            prompt = prompt[:MAX_SINGLE_SKILL_PROMPT_CHARS]
            truncated = True
            warnings.append(f"技能提示词已截断至 {MAX_SINGLE_SKILL_PROMPT_CHARS} 字符")

        section = f"【技能：{name}】\n{prompt}"
        section_chars = len(section)

        # Check total budget
        if total_chars + section_chars > MAX_TOTAL_SKILL_PROMPT_CHARS:
            remaining = MAX_TOTAL_SKILL_PROMPT_CHARS - total_chars
            if remaining < 100:
                # Not enough space, skip
                warnings.append("总技能提示词空间不足，该技能未注入")
                skill_info.append({
                    "name": name,
                    "description": skill.get("description", ""),
                    "truncated": True,
                    "warnings": warnings,
                    "recommended_tools": skill.get("recommended_tools", []),
                    "forbidden_tools": skill.get("forbidden_tools", []),
                    "injected": False,
                    "prompt_fragment": "",
                })
                continue
            # Truncate to fit
            section = section[:remaining]
            truncated = True
            warnings.append(f"技能提示词因总空间限制被截断至 {remaining} 字符")

        sections.append(section)
        total_chars += len(section)

        skill_info.append({
            "name": name,
            "description": skill.get("description", ""),
            "truncated": truncated,
            "warnings": warnings,
            "recommended_tools": skill.get("recommended_tools", []),
            "forbidden_tools": skill.get("forbidden_tools", []),
            "injected": True,
            "prompt_fragment": prompt,
        })

    return "\n\n".join(sections), skill_info


# ── Built-in Skills ───────────────────────────────────────────────────

BUILTIN_SKILLS: list[dict] = [
    {
        "builtin_key": "continue_writing",
        "name": "小说续写",
        "description": "基于已有内容和大纲，自动续写小说正文",
        "trigger_examples": ["续写", "继续写", "接着写", "写下一章", "往下写"],
        "system_prompt": (
            "你正在执行小说续写任务。续写时必须：\n"
            "1. 先搜索当前大纲节点和前文摘要，确保情节连贯。\n"
            "2. 保持角色性格一致性，不要出现OOC。\n"
            "3. 遵循项目的写作风格设定。\n"
            "4. 在续写开头自然衔接上文，不要生硬重复。\n"
            "5. 推进剧情的同时埋下伏笔或制造悬念。"
        ),
        "recommended_tools": ["search_outline", "search_chapters", "search_characters", "chapter_writer", "create_chapter"],
        "scope": "writing",
        "priority": 80,
    },
    {
        "builtin_key": "roleplay",
        "name": "角色扮演",
        "description": "让角色以第一人称回应场景，生成对话或独白",
        "trigger_examples": ["角色扮演", "让角色说", "角色独白", "角色对话", "对白推演"],
        "system_prompt": (
            "你正在执行角色扮演任务。要求：\n"
            "1. 先搜索角色完整档案，了解其性格、说话风格和当前状态。\n"
            "2. 用角色的第一人称视角回应，语言风格要符合角色设定。\n"
            "3. 角色的行为和决策必须与其性格、动机一致。\n"
            "4. 适当加入动作描写和内心独白，不要只有干巴巴的对话。\n"
            "5. 如果涉及多角色互动，使用 dialogue_battle 工具生成回合制对戏。"
        ),
        "recommended_tools": ["search_characters", "roleplay_character", "dialogue_battle"],
        "scope": "writing",
        "priority": 70,
    },
    {
        "builtin_key": "setting_check",
        "name": "设定检查",
        "description": "检查世界观设定的一致性和逻辑性",
        "trigger_examples": ["设定检查", "世界观冲突", "检查设定", "设定一致"],
        "system_prompt": (
            "你正在执行设定检查任务。要求：\n"
            "1. 使用 detect_worldbuilding_conflicts 检测世界观条目间的矛盾。\n"
            "2. 检查时间线是否一致，地理设定是否自洽。\n"
            "3. 检查角色能力是否超出世界观规则体系的限制。\n"
            "4. 列出所有发现的问题，并给出修改建议。\n"
            "5. 如果没有发现问题，明确告知用户当前设定是自洽的。"
        ),
        "recommended_tools": ["search_worldbuilding", "detect_worldbuilding_conflicts", "search_characters"],
        "scope": "worldbuilding",
        "priority": 60,
    },
    {
        "builtin_key": "outline_planning",
        "name": "大纲规划",
        "description": "规划和优化小说大纲结构",
        "trigger_examples": ["大纲规划", "规划大纲", "故事结构", "剧情走向"],
        "system_prompt": (
            "你正在执行大纲规划任务。要求：\n"
            "1. 先搜索完整大纲树和已有章节，了解当前故事进度。\n"
            "2. 分析故事的三幕结构，检查当前处于哪个阶段。\n"
            "3. 规划后续大纲时注意：起承转合、伏笔回收、角色弧线推进。\n"
            "4. 每个大纲节点要包含：标题、摘要、涉及角色。\n"
            "5. 使用 outline_writer 生成大纲节点，确保节奏合理。"
        ),
        "recommended_tools": ["search_outline", "search_outline_tree", "search_chapters", "outline_writer", "create_outline_nodes", "create_outline_node"],
        "scope": "outline",
        "priority": 65,
    },
    {
        "builtin_key": "research_digest",
        "name": "资料搜索整理",
        "description": "搜索和整理项目中的各类资料",
        "trigger_examples": ["搜索资料", "找一下", "查一下", "整理资料"],
        "system_prompt": (
            "你正在执行资料搜索整理任务。要求：\n"
            "1. 根据用户需求，使用对应的搜索工具查找资料。\n"
            "2. 先用 list_* 轻量工具确认数据概况，再用 search_* 获取详情。\n"
            "3. 搜索结果要以清晰的格式呈现给用户。\n"
            "4. 如果用户要求整理，按类别归纳并给出总结。\n"
            "5. 搜索到有用信息后，可以主动用 remember 保存重要资料。"
        ),
        "recommended_tools": [
            "list_characters", "list_chapters", "list_worldbuilding",
            "search_characters", "search_chapters", "search_worldbuilding",
            "search_outline", "search_context", "web_search", "remember",
        ],
        "scope": "global",
        "priority": 50,
    },
    {
        "builtin_key": "forbidden_style_check",
        "name": "禁用句式审校",
        "description": "检测并修复文本中的AI禁用句式",
        "trigger_examples": ["禁用句式", "AI味", "去AI味", "审校", "检查句式"],
        "system_prompt": (
            "你正在执行禁用句式审校任务。要求：\n"
            "1. 使用 detect_forbidden_patterns 检测文本中的禁用句式。\n"
            "2. 逐条列出发现的禁用句式及其位置。\n"
            "3. 对每个禁用句式给出改写建议。\n"
            "4. 如果用户要求自动修复，使用 rewrite_text 工具改写。\n"
            "5. 改写后再次检测，确保禁用句式已被消除。"
        ),
        "recommended_tools": ["detect_forbidden_patterns", "rewrite_text", "search_chapters"],
        "scope": "writing",
        "priority": 55,
    },
]


def ensure_builtin_skills(db: Session, project_id: str) -> None:
    """Seed built-in skills for a project if not already present.

    Uses builtin_key for upsert — no duplicate creation.
    """
    for builtin in BUILTIN_SKILLS:
        existing = (
            db.query(Skill)
            .filter(
                Skill.project_id == project_id,
                Skill.builtin_key == builtin["builtin_key"],
            )
            .first()
        )
        if existing:
            continue

        skill = Skill(
            project_id=project_id,
            builtin_key=builtin["builtin_key"],
            name=builtin["name"],
            description=builtin["description"],
            trigger_examples=json.dumps(builtin["trigger_examples"], ensure_ascii=False),
            system_prompt=builtin["system_prompt"],
            recommended_tools=json.dumps(builtin["recommended_tools"], ensure_ascii=False),
            scope=builtin["scope"],
            priority=builtin["priority"],
            enabled=True,
            is_builtin=True,
        )
        db.add(skill)
        db.flush()
        record_skill_version(
            db,
            skill,
            title="创建内置技能",
            change_summary="首次初始化内置技能",
        )

    commit_session(db)
    logger.info("Ensured built-in skills for project %s", project_id)
