"""V2 novel creation workspace contracts and deterministic stage assembly.

The legacy blueprint generator remains responsible for model interaction. This
module turns its result into a resumable, editable session draft and guarantees
that final submission has the same story granularity as later cataloging.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from collections.abc import Callable
from typing import Any

from app.database.models import NovelCreationSession
from app.services.novel_creation_contract import (
    IMPACT_DEPENDENCIES,
    LEGACY_OPENING_OUTLINE_CHAPTER_COUNT,
    OPENING_OUTLINE_CHAPTER_COUNT,
    SCHEMA_VERSION,
    SOFT_DEPENDENCIES,
    STAGE_LABELS,
    STAGE_ORDER,
)
from app.services.novel_creation_compatibility import project_legacy_draft, projected_generation_blockers
from app.services.novel_creation_failures import clear_stage_failure
from app.services.novel_creation_conflicts import artifact_conflict_projection
from app.services.novel_creation_patch import normalize_patch_operation
from app.services.novel_creation_values import requested_volume_count as _requested_volume_count
from app.services.novel_creation_runs import add_run_event, complete_run, confirm_run, create_run, fail_run, serialize_run

_PRESET_ROWS: tuple[tuple[str, str, str, tuple[str, ...], dict[str, Any]], ...] = (
    ("xuanhuan", "玄幻奇幻", "力量体系、升级兑现与世界奇观", ("东方玄幻", "高武世界", "异世大陆", "诡秘奇幻"), {
        "world_tone": "规则清晰、层级可感知，奇观服务于人物选择",
        "story_structure": "成长主线与世界危机双线推进",
        "pacing": "三章一钩、十章一兑现，升级必须带来代价",
        "writing_style": "画面明确，战斗重策略和状态变化",
        "avoid": ["只报境界不写变化", "连续无代价奇遇", "设定说明书式开篇"],
    }),
    ("xianxia", "仙侠武侠", "修行秩序、道心抉择与江湖关系", ("古典仙侠", "凡人流", "江湖武侠", "宗门群像"), {
        "world_tone": "秩序森严但留有破局缝隙，因果能够追溯",
        "story_structure": "个人求道、宗门博弈、天下变局逐级展开",
        "pacing": "修行与人情交替，关键突破提前铺垫",
        "writing_style": "克制有余韵，动作与心理互相映照",
        "avoid": ["境界膨胀失控", "反派排队送经验", "古风词藻堆砌"],
    }),
    ("urban", "都市现实", "身份变化、职业压力与关系张力", ("都市异能", "职场商战", "现实成长", "娱乐明星"), {
        "world_tone": "生活细节可信，资源与规则能形成实际阻力",
        "story_structure": "目标驱动的事业线与关系线交织",
        "pacing": "场景短促、反馈及时，每章有可见推进",
        "writing_style": "口语自然，细节准确，不悬浮",
        "avoid": ["全员降智", "财富数字代替成就感", "职业常识错误"],
    }),
    ("suspense", "悬疑推理", "证据链、认知差与真相重构", ("本格推理", "社会派悬疑", "刑侦探案", "心理惊悚"), {
        "world_tone": "信息公平但解释具有多义性，危险逐步逼近",
        "story_structure": "案件表层、人物秘密、长期谜团三层嵌套",
        "pacing": "每2至3章新增证据或推翻一个判断",
        "writing_style": "精确克制，关键物证可回看验证",
        "avoid": ["结尾空降凶手", "靠失忆藏信息", "侦探凭感觉断案"],
    }),
    ("scifi", "科幻末世", "技术外推、生存选择与文明尺度", ("硬科幻", "星际文明", "末日求生", "赛博朋克"), {
        "world_tone": "技术规则稳定，社会后果比名词更重要",
        "story_structure": "局部生存危机逐步连接文明级问题",
        "pacing": "危机、验证、选择、后果形成循环",
        "writing_style": "概念易懂，技术通过行动和冲突显现",
        "avoid": ["术语替代逻辑", "科技万能解题", "末世资源无限"],
    }),
    ("history", "历史权谋", "制度约束、利益联盟与时代洪流", ("架空历史", "王朝争霸", "官场权谋", "历史穿越"), {
        "world_tone": "制度、交通、信息和资源都产生真实约束",
        "story_structure": "个人立足、集团形成、格局重塑递进",
        "pacing": "谋划与兑现交替，胜利必然留下新债务",
        "writing_style": "清晰沉稳，用行动呈现权力关系",
        "avoid": ["现代常识无成本碾压", "对手集体失智", "朝代细节混用"],
    }),
    ("romance", "言情情感", "关系变化、价值选择与情绪兑现", ("现代言情", "古代言情", "青春校园", "婚恋家庭"), {
        "world_tone": "人物有各自生活重心，关系变化存在现实代价",
        "story_structure": "吸引、试探、错位、共识与选择逐步推进",
        "pacing": "每章至少改变一次关系认知或行动距离",
        "writing_style": "细腻但不反复解释，以动作承载情绪",
        "avoid": ["误会只靠不说话维持", "控制欲包装成深情", "配角只当助攻工具"],
    }),
    ("female_growth", "女性成长", "主体选择、社会关系与自我建立", ("大女主", "年代成长", "女性群像", "家庭伦理"), {
        "world_tone": "困境具体，支持与阻力都来自完整的人",
        "story_structure": "生存站稳、能力建立、关系重构、价值实现",
        "pacing": "阶段目标清楚，成长通过选择和后果体现",
        "writing_style": "有生活质感，不替人物喊口号",
        "avoid": ["成长等于换伴侣", "所有女性互害", "苦难机械叠加"],
    }),
    ("youth", "青春校园", "成长阵痛、伙伴关系与第一次选择", ("校园成长", "竞技青春", "少年群像", "轻喜日常"), {
        "world_tone": "校园规则和家庭背景真实影响人物",
        "story_structure": "阶段赛事或考试串联关系成长",
        "pacing": "轻重场景交替，日常中持续积累变化",
        "writing_style": "清爽具体，对话有年龄感",
        "avoid": ["成年人语气套给少年", "霸凌浪漫化", "只有恋爱没有成长"],
    }),
    ("game", "游戏竞技", "规则博弈、团队协作与胜负兑现", ("电竞职业", "虚拟网游", "无限流", "规则怪谈"), {
        "world_tone": "规则可验证，胜负来自信息、执行与协作",
        "story_structure": "局内目标推动局外身份和长期谜团",
        "pacing": "每个副本有独立目标、反转和状态结算",
        "writing_style": "空间关系清晰，操作结果可追踪",
        "avoid": ["临时新增规则救场", "数值刷屏", "队友只负责惊叹"],
    }),
    ("fanfiction", "同人衍生", "原作事实锁、视角差异与新因果", ("剧情改写", "角色中心", "世界融合", "幕后流"), {
        "world_tone": "尊重原作核心规则，新变量产生连锁后果",
        "story_structure": "原作节点作为压力测试而非照抄路线",
        "pacing": "熟悉节点快速切入，新变化尽早兑现",
        "writing_style": "角色声线可辨，减少原作百科复述",
        "avoid": ["原作角色工具化", "只换主角不改因果", "设定冲突不解释"],
    }),
    ("literary", "现实文学", "人物处境、时代纹理与复杂余味", ("社会现实", "家庭叙事", "地域故事", "成长文学"), {
        "world_tone": "环境有具体劳动、经济和关系结构",
        "story_structure": "以人物选择串联时间变化和关系后果",
        "pacing": "允许留白，但每个场景必须改变理解",
        "writing_style": "准确节制，意象来自生活本身",
        "avoid": ["苦难景观化", "作者代人物说教", "空泛抒情"],
    }),
)


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def get_presets() -> dict[str, Any]:
    categories = []
    for preset_id, label, description, themes, defaults in _PRESET_ROWS:
        categories.append({
            "id": preset_id,
            "label": label,
            "description": description,
            "themes": [{"id": f"{preset_id}:{index + 1}", "label": theme} for index, theme in enumerate(themes)],
            "defaults": {
                **deepcopy(defaults),
                "special_requirements": ["关键设定必须可追溯", "人物状态随章节更新"],
            },
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "categories": categories,
        "platforms": ["番茄小说", "起点中文网", "晋江文学城", "知乎盐选", "长佩文学", "自出版", "暂不确定"],
        "audiences": ["男频读者", "女频读者", "青少年", "成年大众", "类型文学核心读者", "暂不确定"],
        "length_options": [
            {"id": "short", "label": "短篇", "words": 30000, "chapters": 15},
            {"id": "medium", "label": "中篇", "words": 150000, "chapters": 60},
            {"id": "long", "label": "长篇", "words": 600000, "chapters": 240},
            {"id": "serial", "label": "超长连载", "words": 2500000, "chapters": 1000},
        ],
        "stage_order": list(STAGE_ORDER),
        "stage_labels": STAGE_LABELS,
    }


def _preset(preset_id: str | None) -> dict[str, Any] | None:
    for item in get_presets()["categories"]:
        if item["id"] == preset_id:
            return item
    return None


def initialize_session_draft(session: NovelCreationSession, values: dict[str, Any] | None = None) -> dict[str, Any]:
    values = _dict(values)
    preset_id = _text(values.get("preset_id") or session.genre, "free")
    preset = _preset(preset_id)
    defaults = _dict(preset.get("defaults")) if preset else {}
    existing = _dict(session.draft_json)
    form = {
        "brief": _text(values.get("brief") or values.get("user_brief") or session.user_brief),
        "preset_id": preset_id,
        "theme_id": _text(values.get("theme_id")),
        "genre": _text(values.get("genre") or (preset or {}).get("label") or session.genre, "自由创作"),
        "target_audience": _text(values.get("target_audience") or session.target_audience),
        "platform": _text(values.get("platform") or session.platform),
        "target_words": int(values.get("target_words") or 600000),
        "target_chapters": int(values.get("target_chapters") or 240),
        "opening_chapters": OPENING_OUTLINE_CHAPTER_COUNT,
        "world_tone": _text(values.get("world_tone") or defaults.get("world_tone")),
        "story_structure": _text(values.get("story_structure") or defaults.get("story_structure")),
        "pacing": _text(values.get("pacing") or defaults.get("pacing")),
        "writing_style": _text(values.get("writing_style") or defaults.get("writing_style")),
        "special_requirements": _list(values.get("special_requirements") or defaults.get("special_requirements")),
        "avoid": _list(values.get("avoid") or defaults.get("avoid")),
        "author_overrides": _dict(values.get("author_overrides")),
    }
    if existing.get("form"):
        # Legacy drafts may only contain fields that existed in their version.
        # Layer them over current defaults so migration never creates a partial
        # V3 form that later serialization cannot read.
        merged = {**form, **_dict(existing["form"])}
        explicit_keys = set(values)
        if "user_brief" in explicit_keys:
            explicit_keys.add("brief")
        for key in explicit_keys:
            if key in form:
                merged[key] = deepcopy(form[key])
        form = merged
    existing_stages = _dict(existing.get("stages"))
    existing_constraints = _dict(_dict(existing_stages.get("constraints")).get("data"))
    if existing_constraints:
        # Some historical saves updated the constraints artifact without
        # updating draft.form. Treat the edited artifact as the newer source,
        # while still letting explicit form submissions win.
        form = {**form, **deepcopy(existing_constraints)}
        explicit_keys = set(values)
        if "user_brief" in explicit_keys:
            explicit_keys.add("brief")
        for key in explicit_keys:
            if key in form and key in values:
                form[key] = deepcopy(values[key])
            elif key == "brief":
                form[key] = _text(values.get("brief") or values.get("user_brief"))
    existing_opening = _dict(_dict(existing.get("stages")).get("opening_outline")).get("data")
    existing_opening_chapters = _list(_dict(existing_opening).get("chapters"))
    # Unstarted drafts move to the new lightweight three-chapter default. A
    # confirmed/generated historical 15-chapter window keeps its own target so
    # that users can still review or edit it after upgrading.
    form["opening_chapters"] = (
        LEGACY_OPENING_OUTLINE_CHAPTER_COUNT
        if len(existing_opening_chapters) == LEGACY_OPENING_OUTLINE_CHAPTER_COUNT
        else OPENING_OUTLINE_CHAPTER_COUNT
    )
    stages = existing_stages
    for stage in STAGE_ORDER:
        stages.setdefault(stage, {"status": "pending", "data": None, "updated_at": None})
    constraints_state = _dict(stages.get("constraints"))
    stages["constraints"] = {
        **constraints_state,
        "status": constraints_state.get("status") or "generated",
        "data": deepcopy(form),
        "updated_at": constraints_state.get("updated_at") or _now(),
    }
    existing_schema_version = int(existing.get("schema_version") or session.schema_version or 1)
    creation_mode = _text(
        values.get("creation_mode")
        if "creation_mode" in values
        else existing.get("creation_mode"),
        "explore",
    )
    if creation_mode not in {"author_led", "explore"}:
        creation_mode = "explore"
    author_brief = _text(
        values.get("author_brief")
        if "author_brief" in values
        else existing.get("author_brief"),
    )
    author_outline = _text(
        values.get("author_outline")
        if "author_outline" in values
        else existing.get("author_outline"),
    )
    locked_requirements = _list(
        values.get("locked_requirements")
        if "locked_requirements" in values
        else existing.get("locked_requirements")
    )
    if existing_schema_version < 3 and not values.get("creation_mode"):
        # V2 sessions always used the divergent three-card exploration flow.
        creation_mode = "explore"
    if creation_mode == "author_led" and not author_brief:
        author_brief = form["brief"]
    draft = {
        "schema_version": SCHEMA_VERSION,
        "creation_mode": creation_mode,
        "author_brief": author_brief,
        "author_outline": author_outline,
        "locked_requirements": locked_requirements,
        "form": form,
        "concepts": _list(existing.get("concepts")),
        "concept_seeds": _dict(existing.get("concept_seeds")),
        "selected_concept_id": existing.get("selected_concept_id"),
        "stages": stages,
        "quick_mode": bool(existing.get("quick_mode", False)),
        "created_at": existing.get("created_at") or _now(),
        "updated_at": _now(),
    }
    session.schema_version = SCHEMA_VERSION
    session.current_stage = session.current_stage or "constraints"
    session.draft_json = draft
    session.user_brief = form["brief"] or None
    session.genre = form["genre"] or None
    session.target_audience = form["target_audience"] or None
    session.platform = form["platform"] or None
    return draft


def concept_cards(blueprints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for index, blueprint in enumerate(blueprints[:3]):
        protagonist = _dict(blueprint.get("protagonist"))
        coverage = _dict(blueprint.get("requirement_coverage"))
        creative = _dict(blueprint.get("creative_slots"))
        golden = _dict(blueprint.get("golden_three"))
        cards.append({
            "id": f"concept-{index + 1}",
            "source_index": index,
            "title": _text(blueprint.get("title"), f"创意方向 {index + 1}"),
            "subtitle": _text(blueprint.get("subtitle") or blueprint.get("genre_positioning")),
            "logline": _text(blueprint.get("logline") or blueprint.get("premise")),
            "protagonist_seed": {
                "name": _text(protagonist.get("name"), "待命名主角"),
                "identity": _text(protagonist.get("background")),
                "goal": _text(protagonist.get("goal")),
                "lack": _text(protagonist.get("weakness") or protagonist.get("conflict")),
            },
            "world_hook": _text(creative.get("world_rules") or blueprint.get("world_hook") or blueprint.get("premise")),
            "core_conflict": _text(blueprint.get("core_conflict") or protagonist.get("conflict")),
            "story_engine": _text(creative.get("story_engine") or blueprint.get("story_engine")),
            "opening_hook": _text(golden.get("opening_scene") or golden.get("chapter_1")),
            "differentiators": _list(blueprint.get("selling_points"))[:4],
            "risks": _list(blueprint.get("risks"))[:3],
            "coverage": {
                "score": int(coverage.get("score") or 0),
                "covered": _list(coverage.get("covered")),
                "missing": _list(coverage.get("missing")),
            },
        })
    return cards


def attach_concepts(session: NovelCreationSession, blueprints: list[dict[str, Any]]) -> dict[str, Any]:
    draft = deepcopy(initialize_session_draft(session))
    draft["concepts"] = concept_cards(blueprints)
    # Legacy full-blueprint flows retain their source in blueprint_json. Clear
    # compact seeds so a stale compact selection can never win over it.
    draft["concept_seeds"] = {}
    draft["selected_concept_id"] = None
    draft["stages"]["concepts"] = {"status": "generated", "data": {"options": draft["concepts"]}, "updated_at": _now()}
    draft["updated_at"] = _now()
    session.draft_json = deepcopy(draft)
    session.current_stage = "concepts"
    session.status = "reviewing"
    session.revision = int(session.revision or 0) + 1
    from app.services.novel_creation_versions import record_artifact_version

    record_artifact_version(
        session,
        "concepts",
        draft["stages"]["concepts"]["data"],
        revision=int(session.revision or 0),
        status="generated",
        source="legacy_blueprint",
        change_type="generate",
    )
    return draft


def _compact_concept_coverage(card: dict[str, Any]) -> dict[str, Any]:
    required = {
        "一句话梗概": _text(card.get("logline")),
        "主角种子": _dict(card.get("protagonist_seed")),
        "世界钩子": _text(card.get("world_hook")),
        "核心冲突": _text(card.get("core_conflict")),
        "开篇钩子": _text(card.get("opening_hook")),
    }
    covered = [label for label, value in required.items() if value]
    missing = [label for label, value in required.items() if not value]
    total = max(1, len(covered) + len(missing))
    return {"score": round(len(covered) / total * 100), "covered": covered, "missing": missing}


def save_compact_concepts(
    session: NovelCreationSession,
    concepts: list[dict[str, Any]],
    *,
    source: str = "model",
) -> dict[str, Any]:
    """Persist compact concept cards without changing legacy blueprint_json."""
    draft = deepcopy(initialize_session_draft(session))
    cards: list[dict[str, Any]] = []
    seeds: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(concepts):
        if not isinstance(raw, dict):
            continue
        concept_id = _text(raw.get("id"), f"concept-{index + 1}")
        protagonist = _dict(raw.get("protagonist_seed"))
        card = {
            "id": concept_id,
            "source_index": index,
            "title": _text(raw.get("title"), f"创意方向 {index + 1}"),
            "subtitle": _text(raw.get("subtitle")),
            "logline": _text(raw.get("logline")),
            "protagonist_seed": {
                "name": _text(protagonist.get("name"), "待命名主角"),
                "identity": _text(protagonist.get("identity")),
                "goal": _text(protagonist.get("goal")),
                "lack": _text(protagonist.get("lack")),
            },
            "world_hook": _text(raw.get("world_hook")),
            "core_conflict": _text(raw.get("core_conflict")),
            "story_engine": _text(raw.get("story_engine") or raw.get("core_conflict")),
            "opening_hook": _text(raw.get("opening_hook")),
            "differentiators": _list(raw.get("differentiators"))[:3],
            "risks": _list(raw.get("risks"))[:2],
        }
        coverage = raw.get("coverage") if isinstance(raw.get("coverage"), dict) else _compact_concept_coverage(card)
        card["coverage"] = {
            "score": int(coverage.get("score") or 0),
            "covered": _list(coverage.get("covered")),
            "missing": _list(coverage.get("missing")),
        }
        cards.append(card)
        seeds[concept_id] = deepcopy(card)

    expected_count = 1
    if len(cards) != expected_count:
        label = "作者方案" if expected_count == 1 else "轻量创意"
        raise ValueError(f"{label}必须恰好包含{expected_count}张有效创意卡")

    draft["concepts"] = cards
    draft["concept_seeds"] = seeds
    draft["selected_concept_id"] = None
    draft["stages"]["concepts"] = {
        "status": "generated",
        "data": {"options": deepcopy(cards), "selected_concept_id": None},
        "source": source,
        "updated_at": _now(),
    }
    _invalidate_after(draft, "concepts")
    session.current_stage = "concepts"
    session.draft_json = deepcopy(draft)
    session.revision = int(session.revision or 0) + 1
    session.status = "reviewing"
    from app.services.novel_creation_versions import record_artifact_version

    record_artifact_version(
        session,
        "concepts",
        draft["stages"]["concepts"]["data"],
        revision=int(session.revision or 0),
        status="generated",
        source=source,
        change_type="generate",
    )
    return draft["stages"]["concepts"]


def generation_blockers(session: NovelCreationSession, stage: str, draft_override: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Return confirmed-stage prerequisites that prevent a generation run."""
    draft = project_legacy_draft(_dict(draft_override) if draft_override is not None else _dict(session.draft_json), STAGE_ORDER)
    return projected_generation_blockers(draft, stage, STAGE_ORDER, STAGE_LABELS)


def build_stage_flow(session: NovelCreationSession, draft_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project stored stage data into an author-facing, recoverable workflow."""
    draft = project_legacy_draft(_dict(draft_override) if draft_override is not None else _dict(session.draft_json), STAGE_ORDER)
    stages = _dict(draft.get("stages"))
    items: dict[str, dict[str, Any]] = {}

    attention_stage: str | None = None
    for stage in STAGE_ORDER:
        state = _dict(stages.get(stage))
        status = _text(state.get("status"), "pending")
        if attention_stage is None and (status == "stale" or (status == "generated" and state.get("data") is not None)):
            attention_stage = stage

    legacy_stage = session.current_stage if session.current_stage in STAGE_ORDER else None
    if attention_stage is None:
        attention_stage = legacy_stage

    recommended_stage: str | None = None
    if attention_stage:
        attention_state = _dict(stages.get(attention_stage))
        if attention_state.get("status") in {"generated", "stale"}:
            recommended_stage = attention_stage
    if recommended_stage is None:
        for stage in STAGE_ORDER:
            state = _dict(stages.get(stage))
            status = _text(state.get("status"), "pending")
            if status == "confirmed":
                continue
            if not generation_blockers(session, stage):
                recommended_stage = stage
                break
    if recommended_stage is None:
        recommended_stage = "final_review"

    for index, stage in enumerate(STAGE_ORDER):
        state = _dict(stages.get(stage))
        status = _text(state.get("status"), "pending")
        blockers = generation_blockers(session, stage, draft)
        soft_dependencies = _soft_dependencies(draft, stage)
        has_data = state.get("data") is not None
        can_generate = stage not in {"constraints", "concepts"} and not blockers
        can_confirm = has_data and status in {"generated", "stale"} and not blockers
        can_view = (
            can_generate
            or has_data
            or status in {"generated", "confirmed", "stale"}
            or stage in {attention_stage, recommended_stage}
        )
        actions = ["view"] if can_view else []
        if has_data:
            actions.append("edit")
        if can_generate:
            actions.append("regenerate" if has_data else "generate")
        if can_confirm:
            actions.append("confirm")
        items[stage] = {
            "stage": stage,
            "label": STAGE_LABELS[stage],
            "status": status,
            "can_view": can_view,
            "can_generate": can_generate,
            "can_confirm": can_confirm,
            "blocked_by": blockers,
            "soft_dependencies": soft_dependencies,
            "actions": actions,
            "next_stage": STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else None,
        }

    pending_confirmations = [
        stage for stage in STAGE_ORDER
        if items[stage]["status"] in {"generated", "stale"} and items[stage]["can_confirm"]
    ]
    return {
        "attention_stage": attention_stage,
        "recommended_stage": recommended_stage,
        "legacy_current_stage": legacy_stage,
        "pending_confirmations": pending_confirmations,
        "items": items,
    }


def serialize_session(session: NovelCreationSession, include_runs: bool = True) -> dict[str, Any]:
    projected_draft = project_legacy_draft(_dict(session.draft_json), STAGE_ORDER)
    projected_stages = _dict(projected_draft.get("stages"))
    projected_final = _dict(projected_stages.get("final_review"))
    if projected_final.get("data") is not None and projected_final.get("status") in {"generated", "confirmed"}:
        try:
            projected_final["data"] = derive_stage(session, "final_review", projected_draft)
        except ValueError:
            # Listing work contexts must remain available even when an old or
            # partially imported draft contains a stale final-review snapshot
            # without a selected concept. Validation can report that gap when
            # the user opens the work; it must not erase the whole selector.
            pass
        projected_stages["final_review"] = projected_final
        projected_draft["stages"] = projected_stages
    concepts = _dict(_dict(projected_stages.get("concepts")).get("data"))
    concept_options = _list(concepts.get("options"))
    selected_concept_id = _text(concepts.get("selected_concept_id"))
    selected_concept = next(
        (
            _dict(option) for option in concept_options
            if _text(_dict(option).get("id")) == selected_concept_id
        ),
        _dict(concept_options[0]) if concept_options else {},
    )
    display_title = _text(selected_concept.get("title")) or _text(session.user_brief) or "未命名作品"
    data = {
        "id": session.id,
        "source_project_id": session.source_project_id,
        "created_project_id": session.created_project_id,
        "status": session.status,
        "mode": session.mode,
        "schema_version": int(projected_draft.get("schema_version") or session.schema_version or 1),
        "current_stage": session.current_stage,
        "revision": int(session.revision or 0),
        "user_brief": session.user_brief,
        "display_title": display_title,
        "target_audience": session.target_audience,
        "genre": session.genre,
        "platform": session.platform,
        "draft": projected_draft,
        "checkpoints": deepcopy(session.checkpoints_json),
        "last_error": deepcopy(session.last_error_json),
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
    }
    data["stage_flow"] = build_stage_flow(session, projected_draft)
    if include_runs:
        data["runs"] = [serialize_run(run, include_events=False) for run in list(session.stage_runs or [])[-10:]]
    return data


def serialize_creation_artifact(session: NovelCreationSession, stage: str) -> dict[str, Any]:
    """Return one creation artifact with its workflow and provenance metadata."""
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage: {stage}")
    draft = project_legacy_draft(_dict(session.draft_json), STAGE_ORDER)
    state = _dict(_dict(draft.get("stages")).get(stage))
    flow = build_stage_flow(session, draft)["items"][stage]
    locks = _dict(draft.get("artifact_locks"))
    checkpoints = _list(_dict(session.checkpoints_json).get(stage))
    versions = [item for item in session.artifact_versions if item.artifact_key == stage]
    running = next(
        (
            serialize_run(run, include_events=False)
            for run in reversed(list(session.stage_runs or []))
            if run.stage == stage and run.status in {"queued", "running", "paused"}
        ),
        None,
    )
    stored_status = _text(state.get("status"), "pending")
    conflict_projection = artifact_conflict_projection(
        stage=stage,
        stored_status=stored_status,
        stage_runs=session.stage_runs or [],
        current_revision=int(session.revision or 0),
    )
    return {
        "artifact": stage,
        "label": STAGE_LABELS[stage],
        **conflict_projection,
        "data": deepcopy(state.get("data")),
        "source": _text(state.get("source"), "unknown"),
        "updated_at": state.get("updated_at"),
        "stale_reason": state.get("stale_reason"),
        "locked_paths": list(_list(locks.get(stage))),
        "checkpoint_count": len(checkpoints),
        "can_undo": bool(checkpoints),
        "latest_checkpoint_at": checkpoints[-1].get("created_at") if checkpoints else None,
        "version_count": len(versions),
        "latest_version_id": versions[-1].id if versions else None,
        "revision": int(session.revision or 0),
        "flow": deepcopy(flow),
        "running_operation": running,
    }


def list_creation_artifacts(session: NovelCreationSession) -> list[dict[str, Any]]:
    return [serialize_creation_artifact(session, stage) for stage in STAGE_ORDER]


def creation_artifact_dependencies(session: NovelCreationSession, stage: str) -> dict[str, Any]:
    """Describe blockers and retained downstream data that a change may stale."""
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage: {stage}")
    draft = project_legacy_draft(_dict(session.draft_json), STAGE_ORDER)
    downstream = []
    for name in IMPACT_DEPENDENCIES.get(stage, ()):
        state = _dict(_dict(draft.get("stages")).get(name))
        if state.get("data") is not None:
            downstream.append({
                "artifact": name,
                "label": STAGE_LABELS[name],
                "status": _text(state.get("status"), "pending"),
                "effect": "stale",
            })
    return {
        "artifact": stage,
        "hard_dependencies": generation_blockers(session, stage, draft),
        "soft_dependencies": _soft_dependencies(draft, stage),
        "affected_artifacts": downstream,
    }


def _soft_dependencies(draft: dict[str, Any], stage: str) -> list[dict[str, str]]:
    if stage not in STAGE_ORDER:
        return []
    stages = _dict(draft.get("stages"))
    missing: list[dict[str, str]] = []
    for dependency in SOFT_DEPENDENCIES.get(stage, ()):
        status = _text(_dict(stages.get(dependency)).get("status"), "pending")
        if status != "confirmed":
            missing.append({
                "stage": dependency,
                "label": STAGE_LABELS[dependency],
                "reason": "stale" if status == "stale" else "not_confirmed",
                "message": f"缺少已确认的{STAGE_LABELS[dependency]}，仍可生成，但结果可能需要后续校验",
            })
    return missing


def _pointer_parts(path: str) -> list[str]:
    if path in {"", "/"}:
        return []
    if not path.startswith("/"):
        raise ValueError(f"patch path must be a JSON Pointer: {path}")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _path_is_locked(path: str, locked_paths: list[str]) -> bool:
    normalized = path.rstrip("/") or "/"
    return any(
        normalized == lock.rstrip("/")
        or normalized.startswith(lock.rstrip("/") + "/")
        or lock.rstrip("/").startswith(normalized + "/")
        for lock in locked_paths
    )


def _patch_parent(document: Any, parts: list[str]) -> tuple[Any, str]:
    if not parts:
        raise ValueError("the artifact root cannot be removed or appended")
    cursor = document
    for part in parts[:-1]:
        if isinstance(cursor, dict):
            if part not in cursor:
                cursor[part] = {}
            cursor = cursor[part]
        elif isinstance(cursor, list):
            try:
                cursor = cursor[int(part)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"invalid list path segment: {part}") from exc
        else:
            raise ValueError(f"patch path crosses a scalar value: {part}")
    return cursor, parts[-1]


def patch_creation_artifact(
    session: NovelCreationSession,
    stage: str,
    changes: list[dict[str, Any]],
    *,
    source: str = "author",
    validator: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Atomically apply validated, lock-aware operations to one artifact."""
    artifact = serialize_creation_artifact(session, stage)
    if not changes:
        raise ValueError("changes must contain at least one patch operation")
    document = deepcopy(artifact["data"]) if isinstance(artifact.get("data"), dict) else {}
    locked_paths = [str(item) for item in artifact.get("locked_paths") or []]
    summary: list[dict[str, Any]] = []
    for change in changes:
        path, action = normalize_patch_operation(change)
        if action not in {"set", "replace", "append", "remove", "resize"}:
            raise ValueError(f"unsupported patch action: {action}")
        if _path_is_locked(path, locked_paths):
            raise ValueError(f"字段已锁定，不能修改：{path}")
        parts = _pointer_parts(path)
        if not parts and action in {"set", "replace"}:
            value = change.get("value")
            if not isinstance(value, dict):
                raise ValueError("artifact root replacement must be an object")
            document = deepcopy(value)
            summary.append({"path": path or "/", "action": action})
            continue
        parent, key = _patch_parent(document, parts)
        if action in {"set", "replace"}:
            if isinstance(parent, dict):
                parent[key] = deepcopy(change.get("value"))
            elif isinstance(parent, list):
                try:
                    parent[int(key)] = deepcopy(change.get("value"))
                except (ValueError, IndexError) as exc:
                    raise ValueError(f"invalid list index: {key}") from exc
        elif action == "append":
            target = parent.get(key) if isinstance(parent, dict) else parent[int(key)]
            if not isinstance(target, list):
                raise ValueError(f"append target is not a list: {path}")
            target.append(deepcopy(change.get("value")))
        elif action == "remove":
            if isinstance(parent, dict):
                if key not in parent:
                    raise ValueError(f"remove target does not exist: {path}")
                del parent[key]
            elif isinstance(parent, list):
                try:
                    parent.pop(int(key))
                except (ValueError, IndexError) as exc:
                    raise ValueError(f"invalid list index: {key}") from exc
        else:
            target = parent.get(key) if isinstance(parent, dict) else parent[int(key)]
            if not isinstance(target, list):
                raise ValueError(f"resize target is not a list: {path}")
            target_count = int(change.get("target_count", -1))
            if target_count < 0:
                raise ValueError("target_count must be non-negative")
            if target_count < len(target):
                del target[target_count:]
            else:
                fill_value = change.get("fill_value", {})
                target.extend(deepcopy(fill_value) for _ in range(target_count - len(target)))
        summary.append({"path": path, "action": action})
    if validator:
        validator(stage, document)
    affected = creation_artifact_dependencies(session, stage)["affected_artifacts"]
    save_stage(
        session,
        stage,
        document,
        confirm=False,
        source=source,
        change_type="patch",
        change_summary=summary,
    )
    return {
        "artifact": serialize_creation_artifact(session, stage),
        "changes": summary,
        "affected_artifacts": affected,
    }


def set_creation_artifact_locks(
    session: NovelCreationSession,
    stage: str,
    paths: list[str],
    *,
    locked: bool,
) -> dict[str, Any]:
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage: {stage}")
    normalized = {_text(path) for path in paths if _text(path)}
    if any(not path.startswith("/") for path in normalized):
        raise ValueError("locked paths must use JSON Pointer syntax")
    draft = deepcopy(initialize_session_draft(session))
    locks = _dict(draft.get("artifact_locks"))
    existing = {str(item) for item in _list(locks.get(stage))}
    updated = existing | normalized if locked else existing - normalized
    locks[stage] = sorted(updated)
    draft["artifact_locks"] = locks
    draft["updated_at"] = _now()
    session.draft_json = deepcopy(draft)
    session.revision = int(session.revision or 0) + 1
    state = _dict(_dict(draft.get("stages")).get(stage))
    if isinstance(state.get("data"), dict):
        from app.services.novel_creation_versions import record_artifact_version

        record_artifact_version(
            session,
            stage,
            state["data"],
            revision=int(session.revision or 0),
            status=_text(state.get("status"), "generated"),
            source=_text(state.get("source"), "unknown"),
            change_type="lock" if locked else "unlock",
            change_summary=[{"paths": sorted(normalized)}],
        )
    return serialize_creation_artifact(session, stage)


def undo_creation_artifact(session: NovelCreationSession, stage: str) -> dict[str, Any]:
    """Restore the most recent artifact checkpoint without reviving stale dependents."""
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage: {stage}")
    checkpoints = deepcopy(_dict(session.checkpoints_json))
    items = _list(checkpoints.get(stage))
    if not items:
        raise ValueError("当前立项数据没有可撤销的最近修改")
    checkpoint = items.pop()
    data = checkpoint.get("data")
    if not isinstance(data, dict):
        raise ValueError("最近检查点不包含可恢复的结构化数据")
    draft = deepcopy(initialize_session_draft(session))
    current = _dict(_dict(draft.get("stages")).get(stage))
    from app.services.novel_creation_versions import record_artifact_version

    if isinstance(current.get("data"), dict):
        record_artifact_version(
            session,
            stage,
            current["data"],
            revision=int(session.revision or 0),
            status=_text(current.get("status"), "generated"),
            source=_text(current.get("source"), "unknown"),
            change_type="checkpoint_baseline",
        )
    affected = _invalidate_after(draft, stage)
    draft["stages"][stage] = {
        "status": _text(checkpoint.get("status"), "generated"),
        "data": deepcopy(data),
        "source": _text(checkpoint.get("source"), "restored_checkpoint"),
        "updated_at": _now(),
        "restored_from_revision": checkpoint.get("revision"),
    }
    checkpoints[stage] = items
    session.checkpoints_json = checkpoints
    session.draft_json = deepcopy(draft)
    session.revision = int(session.revision or 0) + 1
    session.current_stage = stage
    session.status = "reviewing"
    record_artifact_version(
        session,
        stage,
        data,
        revision=int(session.revision or 0),
        status=_text(checkpoint.get("status"), "generated"),
        source="restored_checkpoint",
        change_type="undo",
        change_summary=[{"restored_revision": checkpoint.get("revision")}],
    )
    from app.services.novel_creation_entities import sync_creation_entities

    sync_creation_entities(
        session,
        stage,
        data,
        revision=int(session.revision or 0),
        source="restored_checkpoint",
    )
    return {
        "artifact": serialize_creation_artifact(session, stage),
        "undone_source": current.get("source"),
        "restored_revision": checkpoint.get("revision"),
        "affected_artifacts": affected,
    }


def patch_session(session: NovelCreationSession, patch: dict[str, Any]) -> dict[str, Any]:
    draft = initialize_session_draft(session)
    before_form = _dict(draft.get("form"))
    before_author_source = {
        "creation_mode": _text(draft.get("creation_mode"), "explore"),
        "author_brief": _text(draft.get("author_brief")),
        "author_outline": _text(draft.get("author_outline")),
        "locked_requirements": _list(draft.get("locked_requirements")),
    }
    if isinstance(patch.get("form"), dict):
        draft["form"].update(deepcopy(patch["form"]))
    selected = patch.get("selected_concept_id")
    if selected is not None:
        if selected and selected not in {item.get("id") for item in draft.get("concepts", [])}:
            raise ValueError("selected_concept_id does not exist in this session")
        draft["selected_concept_id"] = selected or None
    if "quick_mode" in patch:
        draft["quick_mode"] = bool(patch["quick_mode"])
    if "creation_mode" in patch:
        creation_mode = _text(patch.get("creation_mode"), "explore")
        if creation_mode not in {"author_led", "explore"}:
            raise ValueError("creation_mode must be author_led or explore")
        draft["creation_mode"] = creation_mode
    for field in ("author_brief", "author_outline"):
        if field in patch:
            draft[field] = _text(patch.get(field))
    if "locked_requirements" in patch:
        value = patch.get("locked_requirements")
        if not isinstance(value, list):
            raise ValueError("locked_requirements must be a list")
        draft["locked_requirements"] = [_text(item) for item in value if _text(item)]
    if draft["form"] != before_form:
        _invalidate_after(draft, "constraints")
        draft["stages"]["constraints"] = {"status": "generated", "data": deepcopy(draft["form"]), "updated_at": _now()}
    after_author_source = {
        "creation_mode": _text(draft.get("creation_mode"), "explore"),
        "author_brief": _text(draft.get("author_brief")),
        "author_outline": _text(draft.get("author_outline")),
        "locked_requirements": _list(draft.get("locked_requirements")),
    }
    if after_author_source != before_author_source:
        concept_stage = _dict(draft.get("stages", {}).get("concepts"))
        if concept_stage.get("status") in {"generated", "confirmed", "stale"}:
            concept_stage["status"] = "stale"
            concept_stage["stale_reason"] = "作者方案或不可改动设定已修改"
            draft["stages"]["concepts"] = concept_stage
        _invalidate_after(draft, "concepts")
    draft["updated_at"] = _now()
    session.draft_json = deepcopy(draft)
    session.revision = int(session.revision or 0) + 1
    session.user_brief = _text(draft["form"].get("brief")) or None
    session.genre = _text(draft["form"].get("genre")) or None
    session.target_audience = _text(draft["form"].get("target_audience")) or None
    session.platform = _text(draft["form"].get("platform")) or None
    from app.services.novel_creation_versions import record_artifact_version

    if draft["form"] != before_form:
        record_artifact_version(
            session,
            "constraints",
            draft["form"],
            revision=int(session.revision or 0),
            status="generated",
            source="author",
            change_type="session_patch",
        )
    concept_stage = _dict(draft.get("stages", {}).get("concepts"))
    if after_author_source != before_author_source and isinstance(concept_stage.get("data"), dict):
        record_artifact_version(
            session,
            "concepts",
            concept_stage["data"],
            revision=int(session.revision or 0),
            status=_text(concept_stage.get("status"), "stale"),
            source=_text(concept_stage.get("source"), "unknown"),
            change_type="upstream_stale",
        )
    return draft


def _compact_seed_blueprint(seed: dict[str, Any], form: dict[str, Any]) -> dict[str, Any]:
    protagonist = _dict(seed.get("protagonist_seed"))
    title = _text(seed.get("title"), "未命名小说")
    logline = _text(seed.get("logline"))
    world_hook = _text(seed.get("world_hook"))
    core_conflict = _text(seed.get("core_conflict"))
    opening_hook = _text(seed.get("opening_hook"))
    protagonist_name = _text(protagonist.get("name"), "待命名主角")
    return {
        "title": title,
        "subtitle": _text(seed.get("subtitle")),
        "genre": _text(form.get("genre")),
        "genre_positioning": _text(seed.get("subtitle")),
        "logline": logline,
        "premise": logline,
        "core_conflict": core_conflict,
        "protagonist": {
            "name": protagonist_name,
            "goal": _text(protagonist.get("goal")),
            "weakness": _text(protagonist.get("lack")),
            "conflict": core_conflict,
            "background": _text(protagonist.get("identity")),
            "current_location": "故事起点",
        },
        "characters": [],
        "relationships": [],
        "worldbuilding": [{
            "title": "核心世界钩子",
            "dimension": "power_system",
            "content": world_hook,
        }] if world_hook else [],
        "volume_outline": [],
        "outline": [{
            "title": opening_hook or "开篇钩子",
            "summary": opening_hook or core_conflict,
            "node_type": "chapter",
            "purpose": "建立主角的即时压力与持续追读钩子",
        }] if opening_hook or core_conflict else [],
        "golden_three": {"opening_scene": opening_hook, "chapter_1": opening_hook},
        "style_rules": [],
        "forbidden_patterns": _list(form.get("avoid")),
        "risks": _list(seed.get("risks")),
    }


def _selected_blueprint(session: NovelCreationSession, draft_override: dict[str, Any] | None = None) -> dict[str, Any]:
    draft = deepcopy(draft_override) if isinstance(draft_override, dict) else initialize_session_draft(session)
    selected_id = draft.get("selected_concept_id")
    selected = next((item for item in draft.get("concepts", []) if item.get("id") == selected_id), None)
    if not selected:
        raise ValueError("请先选择一个创意方向")
    compact_seed = _dict(draft.get("concept_seeds")).get(selected_id)
    if isinstance(compact_seed, dict):
        return _compact_seed_blueprint(compact_seed, _dict(draft.get("form")))
    blueprints = session.blueprint_json if isinstance(session.blueprint_json, list) else [session.blueprint_json]
    index = int(selected.get("source_index") or 0)
    if index >= len(blueprints) or not isinstance(blueprints[index], dict):
        raise ValueError("当前创意方向缺少完整方案来源，请重新生成方向")
    return deepcopy(blueprints[index])


def _profile(character: dict[str, Any], index: int) -> dict[str, Any]:
    goal = _text(character.get("goal") or character.get("current_goal"))
    conflict = _text(character.get("conflict") or character.get("active_conflict"))
    personality = _text(character.get("personality"))
    return {
        "core_motivation": goal,
        "inner_lack": _text(character.get("weakness") or conflict, "尚未意识到的内在缺口"),
        "core_belief": _text(character.get("belief"), "相信行动能够改变自身处境"),
        "public_persona": _text(character.get("public_persona") or personality),
        "hidden_persona": _text(character.get("hidden_persona"), "在高压下显露的另一面"),
        "reveal_chapter": int(character.get("reveal_chapter") or max(2, index + 2)),
        "moral_taboo": _text(character.get("moral_taboo"), "不主动牺牲无辜者"),
        "voice": _text(character.get("voice"), "句式和措辞与身份、年龄一致"),
        "action_habit": _text(character.get("action_habit"), "紧张时先观察出口和他人反应"),
        "trauma_trigger": _text(character.get("trauma_trigger") or conflict),
    }


def _chapter_title(number: int, value: Any) -> str:
    title = _text(value, f"未命名事件 {number}")
    import re
    title = re.sub(r"^第\s*\d+\s*章[：:\s-]*", "", title).strip()
    return f"第{number}章 {title}"


def _opening_outline_chapter_count(form: dict[str, Any]) -> int:
    try:
        chapter_count = int(form.get("opening_chapters") or 0)
    except (TypeError, ValueError):
        chapter_count = 0
    if chapter_count == LEGACY_OPENING_OUTLINE_CHAPTER_COUNT:
        return LEGACY_OPENING_OUTLINE_CHAPTER_COUNT
    return OPENING_OUTLINE_CHAPTER_COUNT


def _opening_outline(blueprint: dict[str, Any], form: dict[str, Any]) -> dict[str, Any]:
    raw_nodes = [item for item in _list(blueprint.get("outline")) if isinstance(item, dict)]
    chapter_sources = [item for item in raw_nodes if _text(item.get("node_type"), "chapter") == "chapter"]
    protagonist = _dict(blueprint.get("protagonist"))
    protagonist_name = _text(protagonist.get("name"), "主角")
    core_conflict = _text(blueprint.get("core_conflict") or protagonist.get("conflict"), "核心矛盾持续升级")
    location = _text(protagonist.get("current_location"), "故事起点")
    chapters: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    opening_chapter_count = _opening_outline_chapter_count(form)
    for number in range(1, opening_chapter_count + 1):
        source = chapter_sources[number - 1] if number <= len(chapter_sources) else {}
        summary = _text(source.get("summary") or source.get("planned_summary"), f"主角围绕“{core_conflict}”采取新的行动，并承担由此产生的后果。")
        chapter_id = f"chapter-{number:02d}"
        chapter = {
            "client_id": chapter_id,
            "node_type": "chapter",
            "chapter_number": number,
            "title": _chapter_title(number, source.get("title") or f"局势推进 {number}"),
            "summary": summary,
            "planned_summary": summary,
            "purpose": _text(source.get("purpose"), "推进主线并改变人物状态"),
            "parent_index": int(source.get("parent_index") or 0),
            "sort_order": number,
        }
        chapters.append(chapter)
        section_specs = (
            ("压力进入", "建立本章局面与立即目标", "局面由稳定转为受压"),
            ("选择与对抗", "让人物用行动处理核心阻力", "行动制造新的信息或代价"),
            ("后果与钩子", "结算变化并留下下一章驱动力", "本章目标部分兑现但新问题出现"),
        )
        for scene_number, (suffix, purpose, exit_state) in enumerate(section_specs, start=1):
            sections.append({
                "client_id": f"{chapter_id}-section-{scene_number}",
                "parent_client_id": chapter_id,
                "node_type": "section",
                "title": f"{chapter['title']} · {suffix}",
                "summary": f"{purpose}：{summary}",
                "planned_summary": f"{purpose}：{summary}",
                "sort_order": scene_number,
                "metadata": {
                    "scene_number": scene_number,
                    "purpose": purpose,
                    "location": _text(source.get("location"), location),
                    "timeline": f"第{number}章第{scene_number}场",
                    "pov_character": _text(source.get("pov_character"), protagonist_name),
                    "characters": _list(source.get("characters")) or [protagonist_name],
                    "entry_state": "承接上一场景的目标与压力",
                    "exit_state": exit_state,
                    "emotional_residue": "人物对下一步行动形成新的情绪倾向",
                    "unresolved_actions": ["追踪本场景产生的新问题"],
                },
            })
    return {"opening_chapter_count": opening_chapter_count, "chapters": chapters, "sections": sections, "section_rule": "每章3个场景事件，允许作者调整为2至6个"}


def derive_stage(
    session: NovelCreationSession,
    stage: str,
    draft_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage: {stage}")
    draft = deepcopy(draft_override) if isinstance(draft_override, dict) else initialize_session_draft(session)
    form = _dict(draft.get("form"))
    if stage == "constraints":
        return form
    if stage == "concepts":
        selected = draft.get("selected_concept_id")
        return {"options": _list(draft.get("concepts")), "selected_concept_id": selected}
    blueprint = _selected_blueprint(session, draft)
    if stage == "world_style":
        return {
            "writing_style": _text(form.get("writing_style") or blueprint.get("writing_style")),
            "world_tone": _text(form.get("world_tone")),
            "story_structure": _text(form.get("story_structure")),
            "pacing": _text(form.get("pacing")),
            "style_rules": _list(blueprint.get("style_rules")),
            "forbidden_patterns": _list(form.get("avoid")) + _list(blueprint.get("forbidden_patterns")),
            "worldbuilding": _list(blueprint.get("worldbuilding")),
            "display_groups": ["世界规则", "力量与资源", "社会与文化", "历史与冲突", "生活与感官"],
        }
    if stage == "characters":
        protagonist = _dict(blueprint.get("protagonist"))
        protagonist["role_type"] = "protagonist"
        rows = [protagonist] + [item for item in _list(blueprint.get("characters")) if isinstance(item, dict)]
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, row in enumerate(rows):
            name = _text(row.get("name"))
            if not name or name in seen:
                continue
            seen.add(name)
            item = deepcopy(row)
            item["profile"] = _profile(item, index)
            unique.append(item)
        return {"characters": unique, "relationships": _list(blueprint.get("relationships"))}
    if stage == "locations":
        entries = [item for item in _list(blueprint.get("worldbuilding")) if isinstance(item, dict)]
        locations = [item for item in entries if _text(item.get("dimension")) in {"geography", "factions", "location", "organization"}]
        if not locations:
            locations = entries[:6]
        relations = []
        for index in range(max(0, len(locations) - 1)):
            relations.append({
                "source_title": _text(locations[index].get("title")),
                "target_title": _text(locations[index + 1].get("title")),
                "relation_type": "influences" if index % 2 else "connected_to",
                "description": "双方在资源、通行或权力上互相影响",
                "metadata": {"stable": True, "source": "novel_creation_v2"},
            })
        return {"entries": locations, "relations": relations}
    if stage == "macro_outline":
        volumes = _list(blueprint.get("volume_outline"))
        target_chapters = int(form.get("target_chapters") or 240)
        requested_volume_count = _requested_volume_count(draft)
        volume_count = requested_volume_count or min(12, max(3, round(target_chapters / 100)))
        volumes = volumes[:volume_count]
        while len(volumes) < volume_count:
            index = len(volumes)
            volumes.append({
                "title": f"第{index + 1}卷 阶段转折",
                "summary": f"围绕全书核心冲突完成第{index + 1}阶段的目标、代价与格局变化。",
            })
        span = max(1, target_chapters // len(volumes))
        for index, volume in enumerate(volumes):
            if not isinstance(volume, dict):
                volume = {"title": f"第{index + 1}卷 阶段转折", "summary": "完成本阶段的目标与代价结算。"}
                volumes[index] = volume
            volume["start_chapter"] = index * span + 1
            volume["end_chapter"] = target_chapters if index == len(volumes) - 1 else (index + 1) * span
        return {
            "story_overview": _text(blueprint.get("premise") or blueprint.get("logline")),
            "core_conflict": _text(blueprint.get("core_conflict")),
            "ending_direction": _text(blueprint.get("ending_direction"), "主角必须以最终选择回应开篇提出的核心问题"),
            "target_chapters": target_chapters,
            "requested_volume_count": requested_volume_count,
            "volumes": volumes,
            "stage_plan": [{"name": _text(item.get("title")), "range": [item.get("start_chapter"), item.get("end_chapter")], "promise": _text(item.get("summary"))} for item in volumes if isinstance(item, dict)],
        }
    if stage == "opening_outline":
        return _opening_outline(blueprint, form)
    stages = draft.get("stages", {})
    opening = _dict(stages.get("opening_outline", {}).get("data")) or _opening_outline(blueprint, form)
    characters = _dict(stages.get("characters", {}).get("data")) or derive_stage(session, "characters", draft)
    world = _dict(stages.get("world_style", {}).get("data")) or derive_stage(session, "world_style", draft)
    blocking = []
    for required_stage in ("constraints", "concepts", "world_style", "characters", "locations", "macro_outline", "opening_outline"):
        status = _dict(stages.get(required_stage)).get("status")
        if status != "confirmed":
            blocking.append(f"{STAGE_LABELS[required_stage]}尚未确认或需要重新生成")
    opening_chapter_count = _opening_outline_chapter_count(form)
    if len(opening.get("chapters", [])) != opening_chapter_count:
        blocking.append(f"前{opening_chapter_count}章细纲不完整")
    section_counts: dict[str, int] = {}
    for section in opening.get("sections", []):
        parent = _text(section.get("parent_client_id"))
        section_counts[parent] = section_counts.get(parent, 0) + 1
    chapter_ids = [
        _text(chapter.get("client_id"))
        for chapter in opening.get("chapters", [])
        if isinstance(chapter, dict)
    ]
    if any(not chapter_id or section_counts.get(chapter_id, 0) not in range(2, 7) for chapter_id in chapter_ids):
        blocking.append("每章必须包含2至6个场景事件")
    if not characters.get("characters"):
        blocking.append("缺少角色档案")
    if not world.get("worldbuilding"):
        blocking.append("缺少世界观条目")
    return {
        "ready": not blocking,
        "blocking": blocking,
        "warnings": ["后续章节仅保留宏观卷纲，写作前再按批次展开细纲"],
        "counts": {
            "characters": len(characters.get("characters", [])),
            "worldbuilding": len(world.get("worldbuilding", [])),
            "chapters": len(opening.get("chapters", [])),
            "sections": len(opening.get("sections", [])),
        },
    }


def _checkpoint(
    session: NovelCreationSession,
    stage: str,
    data: Any,
    *,
    status: str = "generated",
    source: str = "unknown",
) -> None:
    checkpoints = deepcopy(_dict(session.checkpoints_json))
    items = _list(checkpoints.get(stage))
    items.append({
        "revision": int(session.revision or 0),
        "created_at": _now(),
        "status": status,
        "source": source,
        "data": deepcopy(data),
    })
    checkpoints[stage] = items[-3:]
    session.checkpoints_json = checkpoints


def _invalidate_after(draft: dict[str, Any], stage: str) -> list[dict[str, str]]:
    affected: list[dict[str, str]] = []
    for downstream in IMPACT_DEPENDENCIES.get(stage, ()):
        current = _dict(draft.get("stages", {}).get(downstream))
        if current.get("status") in {"generated", "confirmed"}:
            previous_status = _text(current.get("status"), "generated")
            current["status"] = "stale"
            current["stale_reason"] = f"上游阶段“{STAGE_LABELS[stage]}”已修改"
            current["stale_source"] = stage
            draft["stages"][downstream] = current
            affected.append({
                "artifact": downstream,
                "label": STAGE_LABELS[downstream],
                "previous_status": previous_status,
                "effect": "stale",
            })
    return affected


def save_stage(
    session: NovelCreationSession,
    stage: str,
    data: dict[str, Any],
    *,
    confirm: bool = False,
    source: str = "generated",
    change_type: str = "save",
    change_summary: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    operation_id: str | None = None,
    restored_from_version_id: str | None = None,
) -> dict[str, Any]:
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage: {stage}")
    draft = deepcopy(initialize_session_draft(session))
    if stage == "constraints":
        # Keep the compatibility form snapshot and the editable constraints
        # artifact atomic. Partial payloads are layered over the complete form.
        data = {**_dict(draft.get("form")), **deepcopy(data)}
    previous = _dict(draft["stages"].get(stage))
    from app.services.novel_creation_versions import record_artifact_version

    if previous.get("data") is not None:
        _checkpoint(
            session,
            stage,
            previous.get("data"),
            status=_text(previous.get("status"), "generated"),
            source=_text(previous.get("source"), "unknown"),
        )
        record_artifact_version(
            session,
            stage,
            previous.get("data"),
            revision=int(session.revision or 0),
            status=_text(previous.get("status"), "generated"),
            source=_text(previous.get("source"), "unknown"),
            change_type="baseline",
        )
    changed = previous.get("data") != data
    draft["stages"][stage] = {
        "status": "confirmed" if confirm else "generated",
        "data": deepcopy(data),
        "source": source,
        "updated_at": _now(),
    }
    if stage == "constraints":
        draft["form"] = deepcopy(data)
    if stage == "concepts":
        options = data.get("options")
        if isinstance(options, list):
            draft["concepts"] = deepcopy(options)
            if not _list(session.blueprint_json):
                draft["concept_seeds"] = {
                    _text(item.get("id")): deepcopy(item)
                    for item in options
                    if isinstance(item, dict) and _text(item.get("id"))
                }
        selected_id = data.get("selected_concept_id")
        if selected_id:
            draft["selected_concept_id"] = selected_id
    if changed:
        _invalidate_after(draft, stage)
    if confirm:
        next_index = min(STAGE_ORDER.index(stage) + 1, len(STAGE_ORDER) - 1)
        session.current_stage = STAGE_ORDER[next_index]
    else:
        session.current_stage = stage
    session.draft_json = deepcopy(draft)
    session.revision = int(session.revision or 0) + 1
    session.status = "reviewing"
    session.last_error_json = clear_stage_failure(session.last_error_json, stage)
    record_artifact_version(
        session,
        stage,
        data,
        revision=int(session.revision or 0),
        status="confirmed" if confirm else "generated",
        source=source,
        change_type=change_type,
        change_summary=change_summary,
        run_id=run_id,
        operation_id=operation_id,
        restored_from_version_id=restored_from_version_id,
    )
    from app.services.novel_creation_entities import sync_creation_entities

    sync_creation_entities(
        session,
        stage,
        data,
        revision=int(session.revision or 0),
        source=source,
    )
    return draft["stages"][stage]


def build_apply_blueprint(session: NovelCreationSession) -> dict[str, Any]:
    blueprint = _selected_blueprint(session)
    draft = project_legacy_draft(initialize_session_draft(session), STAGE_ORDER)
    stages = draft.get("stages", {})
    unconfirmed = [
        STAGE_LABELS[name]
        for name in ("constraints", "concepts", "world_style", "characters", "locations", "macro_outline", "opening_outline")
        if _dict(stages.get(name)).get("status") != "confirmed"
    ]
    if unconfirmed:
        raise ValueError("以下阶段尚未确认或已失效：" + "、".join(unconfirmed))
    final = derive_stage(session, "final_review", draft)
    if not final.get("ready"):
        raise ValueError("最终审阅未通过：" + "；".join(final.get("blocking", [])))
    characters = _dict(stages.get("characters", {}).get("data")) or derive_stage(session, "characters")
    world = _dict(stages.get("world_style", {}).get("data")) or derive_stage(session, "world_style")
    locations = _dict(stages.get("locations", {}).get("data")) or derive_stage(session, "locations")
    macro = _dict(stages.get("macro_outline", {}).get("data")) or derive_stage(session, "macro_outline")
    opening = _dict(stages.get("opening_outline", {}).get("data")) or derive_stage(session, "opening_outline")
    character_rows = _list(characters.get("characters"))
    protagonist = next((row for row in character_rows if row.get("role_type") == "protagonist"), character_rows[0] if character_rows else {})
    supporting = [row for row in character_rows if row is not protagonist]
    all_world = _list(world.get("worldbuilding"))
    known_titles = {_text(item.get("title")) for item in all_world if isinstance(item, dict)}
    all_world.extend(item for item in _list(locations.get("entries")) if isinstance(item, dict) and _text(item.get("title")) not in known_titles)
    blueprint.update({
        "protagonist": protagonist,
        "characters": supporting,
        "relationships": _list(characters.get("relationships")),
        "writing_style": _text(world.get("writing_style") or blueprint.get("writing_style")),
        "world_tone": _text(world.get("world_tone") or blueprint.get("world_tone")),
        "story_structure": _text(world.get("story_structure") or blueprint.get("story_structure")),
        "pacing": _text(world.get("pacing") or blueprint.get("pacing")),
        "style_rules": _list(world.get("style_rules")) or _list(blueprint.get("style_rules")),
        "forbidden_patterns": _list(world.get("forbidden_patterns")) or _list(blueprint.get("forbidden_patterns")),
        "worldbuilding": all_world,
        "worldbuilding_relations": _list(locations.get("relations")),
        "volume_outline": _list(macro.get("volumes")),
        "outline": _list(opening.get("chapters")) + _list(opening.get("sections")),
        "novel_creation_schema_version": SCHEMA_VERSION,
    })
    return blueprint
