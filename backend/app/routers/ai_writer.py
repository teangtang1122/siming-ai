"""AI Writing Engine — narrator, character dialogue, dialogue battle, text ops, conflict, changes."""
import json
import asyncio
import re
from datetime import datetime, timedelta
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..ai.gateway import LLMGateway
from ..core.db_helpers import get_character_or_404, get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError, LLMError
from ..core.response import ApiResponse
from ..database.models import (
    Chapter,
    ChapterCharacter,
    ChapterSnapshot,
    ChapterSummary,
    Character,
    CharacterChangeLog,
    CharacterRelationship,
    CharacterTimeline,
    AssistantConversation,
    AssistantMessage,
    OutlineNode,
    OutlineNodeCharacter,
    Project,
)
from ..database.session import get_db
from ..services.context_builders import (
    _build_chapter_detail_context,
    _build_character_ai_context,
    _build_character_catalog,
    _build_character_context,
    _build_character_relationships,
    _build_character_timeline,
    _build_outline_context,
    _build_outline_overview,
    _build_recent_chapter_details,
    _build_recent_summaries,
    _build_relationship_context,
    _build_scene_characters_context,
    _build_world_context,
    _count_words,
    _get_outline_node_or_404,
)
from ..prompts.workspace_assistant import (
    build_workspace_assistant_system_prompt,
    build_workspace_assistant_initial_user_message,
    format_tool_result_message,
    format_previous_search_context,
    _compress_search_result,
    MAX_ITERATIONS,
)
from ..prompts.style_prompts import build_style_context
from ..services.style_rules import (
    STYLE_OPTIONS,
    _detect_forbidden_sentence_violations,
    _repair_assistant_parsed_style,
    _repair_forbidden_sentence_text,
)
from ..services.workspace.tool_schemas import (
    ALL_TOOL_SCHEMAS,
    SEARCH_TOOL_NAMES,
    WRITE_TOOL_NAMES,
)
from ..services.workspace import (
    _character_payload,
    _find_character_by_name_or_id,
    _find_outline_by_title_or_id,
    _outline_node_payload,
    execute_workspace_action,
)
from ..schemas.ai_writer import WorkspaceAssistantRequest

router = APIRouter(tags=["ai-writer"])

def _strip_json_fences(text: str) -> str:
    value = (text or "").strip()
    # Remove markdown code fences
    for _ in range(2):
        if value.startswith("```json"):
            value = value[7:]
        elif value.startswith("```"):
            value = value[3:]
        if value.endswith("```"):
            value = value[:-3]
    return value.strip()


def _escape_json_string_values(text: str) -> str:
    """Escape unescaped ASCII double-quotes inside JSON string values.

    Scans the text tracking in-string / out-of-string state and escape mode.
    When a double-quote appears inside a string and is NOT followed by a JSON
    structural character (, } ] :), it is treated as an accidental unescaped
    quote (e.g. from Chinese dialogue) and escaped as \\\".
    """
    result: list[str] = []
    in_string = False
    escape_next = False
    i = 0
    while i < len(text):
        ch = text[i]
        if not in_string:
            result.append(ch)
            if ch == '"':
                in_string = True
            i += 1
        else:
            if escape_next:
                result.append(ch)
                escape_next = False
                i += 1
            elif ch == '\\':
                result.append(ch)
                escape_next = True
                i += 1
            elif ch == '"':
                # Potential string terminator — look ahead for structural char
                ahead = i + 1
                while ahead < len(text) and text[ahead].isspace():
                    ahead += 1
                if ahead >= len(text) or text[ahead] in ',}:]':
                    # Real string terminator
                    in_string = False
                    result.append(ch)
                else:
                    # Unescaped quote inside string — escape it
                    result.append('\\')
                    result.append('"')
                i += 1
            else:
                result.append(ch)
                i += 1
    return ''.join(result)


def _parse_json_object(text: str) -> Optional[dict]:
    cleaned = _strip_json_fences(text)

    def _try_parse(candidate_text: str) -> Optional[dict]:
        start = candidate_text.find("{")
        if start < 0:
            return None
        for end_offset in range(len(candidate_text), start + 1, -1):
            end = candidate_text.rfind("}", start, end_offset)
            if end < 0:
                continue
            candidate = candidate_text[start:end + 1]
            try:
                parsed = json.loads(candidate, strict=False)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    parsed = _try_parse(cleaned)
    if parsed is not None:
        return parsed
    # Escape unescaped quotes inside string values and retry
    escaped = _escape_json_string_values(cleaned)
    if escaped != cleaned:
        return _try_parse(escaped)
    return None


WORKSPACE_JSON_REPAIR_SYSTEM_PROMPT = (
    "你是JSON修复器，只修复语法，不改写正文，不增删工具动作。"
    "输入是小说项目助手返回的近似JSON，可能因为章节正文里的引号、换行或尾随文本导致无法解析。"
    "请把它修复为一个可被 json.loads 解析的合法JSON对象。"
    "必须保留 reply、done、actions、needs_confirmation 字段；actions 内的工具名和参数必须尽量原样保留。"
    "只输出JSON对象，不要Markdown，不要解释。"
)


async def _repair_workspace_json_output(raw_text: str, model: Optional[str]) -> Optional[dict]:
    """Repair near-JSON workspace assistant output once before dropping actions."""
    if not raw_text.strip():
        return None
    try:
        result = await LLMGateway.chat_completion(
            messages=[
                {"role": "system", "content": WORKSPACE_JSON_REPAIR_SYSTEM_PROMPT},
                {"role": "user", "content": raw_text[:120_000]},
            ],
            model=model,
            temperature=0,
            timeout=90,
            retry=0,
        )
    except Exception:
        return None
    return _parse_json_object(result.get("content", ""))


def _assistant_heuristic_plan(message: str) -> dict:
    text = message.lower()
    tools = {"read_recent_summaries", "read_outline", "read_worldbuilding", "read_characters", "read_relationships"}
    if any(key in text for key in ["矛盾", "冲突", "合理", "检查", "详细", "正文", "bug", "不一致"]):
        tools.add("read_chapter_detail")
    if any(key in text for key in ["写", "生成", "新章节", "创建章节", "对话", "扮演", "行动", "出场"]):
        tools.add("roleplay_characters")
    should_create = bool(
        any(key in text for key in ["创建章节", "新章节", "直接生成章节", "写一章", "写第", "帮我写第"])
        or re.search(r"写\s*第?\s*\d+\s*章", text)
        or re.search(r"第\s*\d+\s*章", text) and any(key in text for key in ["写", "生成", "创建"])
    )
    return {
        "intent": "write" if should_create else "advise",
        "tools": sorted(tools),
        "character_names": [],
        "needs_worldbuilding": any(key in text for key in ["设定", "世界观", "规则", "势力", "地图"]),
        "should_create_chapter": should_create,
        "chapter_title": _chapter_title_from_request(message) if should_create else "",
        "reason": "启发式计划",
    }


def _chapter_title_from_request(message: str) -> str:
    text = (message or "").strip()
    match = re.search(r"第\s*([0-9一二两三四五六七八九十百千万零〇]+)\s*章", text)
    if match:
        return f"第{match.group(1)}章"
    return "AI生成章节"


def _normalize_assistant_plan(raw_plan: Optional[dict], message: str) -> dict:
    fallback = _assistant_heuristic_plan(message)
    if not raw_plan:
        return fallback
    allowed_tools = {
        "read_recent_summaries",
        "read_outline",
        "read_worldbuilding",
        "read_characters",
        "read_relationships",
        "read_chapter_detail",
        "roleplay_characters",
    }
    tools = [tool for tool in raw_plan.get("tools") or [] if tool in allowed_tools]
    for tool in fallback["tools"]:
        if tool not in tools:
            tools.append(tool)
    names = [
        str(name).strip()
        for name in raw_plan.get("character_names") or []
        if str(name).strip()
    ][:6]
    return {
        "intent": str(raw_plan.get("intent") or fallback["intent"])[:50],
        "tools": tools,
        "character_names": names,
        "needs_worldbuilding": bool(raw_plan.get("needs_worldbuilding", fallback["needs_worldbuilding"])),
        "should_create_chapter": bool(raw_plan.get("should_create_chapter")) or bool(fallback["should_create_chapter"]),
        "chapter_title": str(raw_plan.get("chapter_title") or fallback.get("chapter_title") or _chapter_title_from_request(message) or "")[:200],
        "reason": str(raw_plan.get("reason") or fallback["reason"])[:500],
    }


def _resolve_assistant_characters(
    db: Session,
    project_id: str,
    names: list[str],
    outline_node_id: Optional[str],
    limit: int = 4,
) -> list[Character]:
    resolved: list[Character] = []
    seen: set[str] = set()
    clean_names = {name.strip() for name in names if name.strip()}
    if clean_names:
        characters = (
            db.query(Character)
            .filter(Character.project_id == project_id, Character.name.in_(clean_names))
            .all()
        )
        for character in characters:
            resolved.append(character)
            seen.add(character.id)
    if outline_node_id and len(resolved) < limit:
        links = (
            db.query(OutlineNodeCharacter)
            .join(OutlineNode, OutlineNode.id == OutlineNodeCharacter.outline_node_id)
            .filter(OutlineNode.project_id == project_id, OutlineNodeCharacter.outline_node_id == outline_node_id)
            .all()
        )
        for link in links:
            if link.character and link.character.id not in seen:
                resolved.append(link.character)
                seen.add(link.character.id)
            if len(resolved) >= limit:
                break
    if len(resolved) < limit:
        extras = (
            db.query(Character)
            .filter(Character.project_id == project_id)
            .order_by(Character.role_type.asc(), Character.updated_at.desc())
            .limit(limit * 2)
            .all()
        )
        for character in extras:
            if character.id not in seen:
                resolved.append(character)
                seen.add(character.id)
            if len(resolved) >= limit:
                break
    return resolved[:limit]


async def _assistant_character_roleplay(
    db: Session,
    project_id: str,
    character: Character,
    user_message: str,
    outline_ctx: str,
    summaries: str,
    model: Optional[str],
) -> dict:
    project = get_project_or_404(db, project_id)
    messages = [
        {
            "role": "system",
            "content": (
                f"你是小说角色「{character.name}」的角色AI。\n"
                "请根据角色档案、关系和当前剧情判断这个角色是否会主动行动或发言。"
                "只输出JSON，不要输出解释性散文。\n"
                "格式：{\"should_act\":true,\"action_type\":\"dialogue|action|inner|none\",\"content\":\"角色会说/做/想的内容\",\"rationale\":\"为什么符合人设\"}\n\n"
                f"【角色档案】\n{_build_character_context(character)}\n\n"
                f"【角色AI设定】\n{_build_character_ai_context(character)}\n\n"
                f"【关系网】\n{_build_character_relationships(db, project_id, character.id)}\n\n"
                f"【近期经历】\n{_build_character_timeline(db, character.id)}\n\n"
                f"【作品文风约束】\n{build_style_context(project)}\n\n"
                f"【当前大纲】\n{outline_ctx}\n\n"
                f"【前文摘要】\n{summaries}"
            ),
        },
        {"role": "user", "content": user_message},
    ]
    result = await LLMGateway.chat_completion(messages=messages, model=model, temperature=0.6, max_tokens=1200)
    parsed = _parse_json_object(result.get("content", ""))
    if not parsed:
        parsed = {
            "should_act": False,
            "action_type": "none",
            "content": "",
            "rationale": result.get("content", "")[:500],
        }
    return {
        "character_id": character.id,
        "character_name": character.name,
        "should_act": bool(parsed.get("should_act")),
        "action_type": str(parsed.get("action_type") or "none")[:50],
        "content": str(parsed.get("content") or "")[:4000],
        "rationale": str(parsed.get("rationale") or "")[:1000],
    }


def _create_assistant_chapter(
    db: Session,
    project_id: str,
    title: str,
    content: str,
    outline_node_id: Optional[str],
    summary_text: str,
    involved_character_names: list[str],
    model: Optional[str],
) -> Optional[Chapter]:
    title = (title or "").strip()[:200]
    content = (content or "").strip()
    if not title or not content:
        return None
    outline_node = _get_outline_node_or_404(db, project_id, outline_node_id)
    chapter = Chapter(
        project_id=project_id,
        outline_node_id=outline_node.id if outline_node else None,
        title=title,
        content=content,
        word_count=_count_words(content),
        current_version=1,
    )
    db.add(chapter)
    db.flush()
    db.add(ChapterSummary(
        chapter_id=chapter.id,
        summary_text=(summary_text or title)[:20000],
        key_events=None,
        token_count=len(summary_text or title),
        ai_model=model,
    ))
    names = {name.strip() for name in involved_character_names if name and name.strip()}
    if names:
        characters = (
            db.query(Character)
            .filter(Character.project_id == project_id, Character.name.in_(names))
            .all()
        )
        for character in characters:
            db.add(ChapterCharacter(
                chapter_id=chapter.id,
                character_id=character.id,
                appearance_type="AI助手识别",
                description="由自动写作助手创建章节时关联",
            ))
    return chapter


def _chapter_brief(chapter: Chapter) -> dict:
    return {
        "id": chapter.id,
        "title": chapter.title,
        "outline_node_id": chapter.outline_node_id,
        "word_count": chapter.word_count or 0,
    }


def _create_assistant_chapter_placeholder(
    db: Session,
    project_id: str,
    title: str,
    outline_node_id: Optional[str],
) -> Chapter:
    outline_node = _get_outline_node_or_404(db, project_id, outline_node_id)
    clean_title = (title or "AI生成章节").strip()[:200] or "AI生成章节"
    chapter = Chapter(
        project_id=project_id,
        outline_node_id=outline_node.id if outline_node else None,
        title=clean_title,
        content="（AI正在生成正文，完成后会自动写入。）",
        word_count=0,
        current_version=1,
    )
    db.add(chapter)
    db.flush()
    return chapter


def _finalize_assistant_chapter(
    db: Session,
    chapter: Chapter,
    title: str,
    content: str,
    summary_text: str,
    involved_character_names: list[str],
    model: Optional[str],
) -> Chapter:
    clean_title = (title or chapter.title or "AI生成章节").strip()[:200] or "AI生成章节"
    clean_content = (content or "").strip()
    chapter.title = clean_title
    chapter.content = clean_content
    chapter.word_count = _count_words(clean_content)
    chapter.current_version = max(1, chapter.current_version or 1) + 1
    chapter.updated_at = datetime.utcnow()
    db.add(ChapterSnapshot(
        chapter_id=chapter.id,
        version_number=chapter.current_version,
        content=clean_content,
        word_count=chapter.word_count,
        trigger_type="ai_insert",
    ))

    if chapter.summary:
        chapter.summary.summary_text = (summary_text or clean_title)[:20000]
        chapter.summary.key_events = None
        chapter.summary.token_count = len(summary_text or clean_title)
        chapter.summary.ai_model = model
        chapter.summary.updated_at = datetime.utcnow()
    else:
        db.add(ChapterSummary(
            chapter_id=chapter.id,
            summary_text=(summary_text or clean_title)[:20000],
            key_events=None,
            token_count=len(summary_text or clean_title),
            ai_model=model,
        ))

    names = {name.strip() for name in involved_character_names if name and name.strip()}
    if names:
        db.query(ChapterCharacter).filter(ChapterCharacter.chapter_id == chapter.id).delete()
        characters = (
            db.query(Character)
            .filter(Character.project_id == chapter.project_id, Character.name.in_(names))
            .all()
        )
        for character in characters:
            db.add(ChapterCharacter(
                chapter_id=chapter.id,
                character_id=character.id,
                appearance_type="AI助手识别",
                description="由自动写作助手创建章节时关联",
            ))
    return chapter


def _assistant_history_text(history: list[dict], limit: int = 8) -> str:
    lines = []
    for item in (history or [])[-limit:]:
        if not isinstance(item, dict):
            continue
        role = "用户" if item.get("role") == "user" else "助手（已完成）"
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        # Truncate assistant messages aggressively — full planning text confuses the model
        # into thinking the instructions in history are still active tasks
        max_len = 4000 if item.get("role") == "user" else 600
        lines.append(f"{role}：{content[:max_len]}")
    return "\n\n".join(lines) or "暂无对话历史。"


def _assistant_conversation_to_dict(conversation: AssistantConversation, message_count: Optional[int] = None) -> dict:
    return {
        "id": conversation.id,
        "project_id": conversation.project_id,
        "title": conversation.title,
        "scope": conversation.scope,
        "current_chapter_id": conversation.current_chapter_id,
        "current_outline_node_id": conversation.current_outline_node_id,
        "model": conversation.model,
        "message_count": message_count,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def _assistant_message_to_dict(message: AssistantMessage) -> dict:
    payload = None
    if message.payload_json:
        try:
            payload = json.loads(message.payload_json)
        except Exception:
            payload = None
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "payload": payload,
        "status": message.status,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "updated_at": message.updated_at.isoformat() if message.updated_at else None,
    }


def _get_assistant_conversation_or_404(
    db: Session,
    project_id: str,
    conversation_id: str,
) -> AssistantConversation:
    conversation = (
        db.query(AssistantConversation)
        .filter(
            AssistantConversation.id == conversation_id,
            AssistantConversation.project_id == project_id,
        )
        .first()
    )
    if not conversation:
        raise NotFoundError("助手对话不存在")
    return conversation


def _assistant_history_from_messages(
    db: Session,
    conversation_id: str,
    before_message_id: Optional[str] = None,
    limit: int = 8,
) -> str:
    messages = (
        db.query(AssistantMessage)
        .filter(AssistantMessage.conversation_id == conversation_id)
        .order_by(
            AssistantMessage.created_at.asc(),
            AssistantMessage.role.desc(),
            AssistantMessage.updated_at.asc(),
            AssistantMessage.id.asc(),
        )
        .all()
    )
    history: list[dict] = []
    for message in messages:
        if before_message_id and message.id == before_message_id:
            break
        if message.status not in {"completed", "running"}:
            continue
        history.append({"role": message.role, "content": message.content})
    return _assistant_history_text(history, limit=limit)


def _previous_search_context_from_messages(
    db: Session,
    conversation_id: str,
    before_message_id: Optional[str] = None,
) -> str:
    """Extract and merge persisted search results from ALL prior assistant messages in this conversation."""
    messages = (
        db.query(AssistantMessage)
        .filter(
            AssistantMessage.conversation_id == conversation_id,
            AssistantMessage.role == "assistant",
            AssistantMessage.status.in_({"completed", "running"}),
        )
        .order_by(AssistantMessage.created_at.desc())
        .all()
    )
    # Merge by tool, deduplicate data entries by id, keep most recent
    merged: dict[str, dict] = {}
    seen_ids: dict[str, set] = {}  # tool -> set of seen entry ids
    for message in messages:
        if before_message_id and message.id == before_message_id:
            continue
        if not message.payload_json:
            continue
        try:
            payload = json.loads(message.payload_json)
        except Exception:
            continue
        ctx = payload.get("searched_context")
        if not isinstance(ctx, list):
            continue
        for group in ctx:
            if not isinstance(group, dict):
                continue
            tool = str(group.get("tool") or "?")
            data = group.get("data")
            if not isinstance(data, list):
                continue
            if tool not in merged:
                merged[tool] = {"tool": tool, "detail": str(group.get("detail") or ""), "data": []}
                seen_ids[tool] = set()
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                eid = entry.get("id", "")
                if eid and eid in seen_ids[tool]:
                    continue
                if eid:
                    seen_ids[tool].add(eid)
                merged[tool]["data"].append(entry)
    all_search_results = list(merged.values())
    return format_previous_search_context(all_search_results)


def _assistant_title_from_message(message: str) -> str:
    title = " ".join((message or "").strip().split())
    if not title:
        return "新对话"
    return title[:36] + ("..." if len(title) > 36 else "")


async def _execute_workspace_action(db: Session, project_id: str, action: dict) -> dict:
    """Execute a workspace tool action, with pre-flight forbidden-pattern check for chapter creation."""
    tool = str(action.get("tool") or "").strip()
    args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}

    if tool == "create_chapter" and args.get("content"):
        project = get_project_or_404(db, project_id)
        violations = _detect_forbidden_sentence_violations(str(args.get("content")), project)
        if violations:
            try:
                model = str(args.get("model") or "") or None
                repaired, before, remaining = await _repair_forbidden_sentence_text(
                    str(args.get("content")),
                    project,
                    model,
                    None,
                )
                args = {**args, "content": repaired}
                action = {**action, "arguments": args}
            except Exception:
                pass

    return await execute_workspace_action(db, project_id, action)


def _is_affirmative_confirmation(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if any(phrase in normalized for phrase in ["不是", "不行", "不要", "不按", "不可以", "否", "换个方向", "改一下"]):
        return False
    return any(
        phrase in normalized
        for phrase in [
            "是",
            "可以",
            "确认",
            "同意",
            "按这个",
            "就这样",
            "继续",
            "没问题",
            "照这个",
            "就按",
            "yes",
            "ok",
        ]
    ) or normalized in {"好", "好的", "行"}


def _user_requests_chapter_creation(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return any(
        phrase in normalized
        for phrase in ["写第", "写一章", "写新章", "新章节", "创建章节", "生成章节", "帮我写", "开始写", "续写第"]
    )


def _chapter_action_needs_outline_confirmation(
    db: Session,
    project_id: str,
    actions: list[dict],
    user_message: str,
) -> bool:
    confirmed = _is_affirmative_confirmation(user_message)
    pending_outline_titles = set()
    for action in actions:
        if isinstance(action, dict) and action.get("tool") == "create_outline_node":
            args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
            title = str(args.get("title") or "").strip()
            if title:
                pending_outline_titles.add(title)
    if pending_outline_titles and _user_requests_chapter_creation(user_message) and not confirmed:
        return True
    for action in actions:
        if not isinstance(action, dict) or action.get("tool") != "create_chapter":
            continue
        args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
        outline_ref = args.get("outline_node_id") or args.get("outline_node_title") or args.get("outline_title")
        if _find_outline_by_title_or_id(db, project_id, outline_ref):
            continue
        if confirmed and str(outline_ref or "").strip() in pending_outline_titles:
            continue
        if confirmed and len(pending_outline_titles) == 1 and not str(outline_ref or "").strip():
            args["outline_node_title"] = next(iter(pending_outline_titles))
            continue
        if not confirmed:
            return True
        return True
    return False


# ---------------------------------------------------------------------------
# SSE streaming helper
# ---------------------------------------------------------------------------

async def _sse_writer_stream(
    generator: AsyncGenerator[str, None],
    project: Optional[Project] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    full_text = ""
    try:
        async for chunk in generator:
            full_text += chunk
            data = json.dumps({"type": "token", "content": chunk}, ensure_ascii=False, separators=(",", ":"))
            yield f"data: {data}\n\n"
        if project:
            violations = _detect_forbidden_sentence_violations(full_text, project)
            if violations:
                yield _sse_event({
                    "type": "style_check",
                    "status": "running",
                    "message": f"发现 {len(violations)} 处禁用句式，正在自动修订",
                    "violations": violations[:8],
                })
                try:
                    repaired, before, remaining = await _repair_forbidden_sentence_text(
                        full_text,
                        project,
                        model,
                        max_tokens,
                    )
                    full_text = repaired
                    yield _sse_event({
                        "type": "style_repaired",
                        "status": "ok" if not remaining else "warning",
                        "message": "禁用句式已自动修订" if not remaining else f"仍有 {len(remaining)} 处需要人工确认",
                        "full_text": full_text,
                        "violations": before[:8],
                        "remaining": remaining[:8],
                    })
                except Exception as exc:
                    yield _sse_event({
                        "type": "style_repaired",
                        "status": "error",
                        "message": f"禁用句式自动修订失败：{exc}",
                        "full_text": full_text,
                        "violations": violations[:8],
                    })
        done_data = json.dumps(
            {"type": "done", "full_text": full_text},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        yield f"data: {done_data}\n\n"
        yield "data: [DONE]\n\n"
    except LLMError as e:
        error_data = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False, separators=(",", ":"))
        yield f"data: {error_data}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        error_data = json.dumps(
            {"type": "error", "message": f"服务器错误: {e}"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        yield f"data: {error_data}\n\n"
        yield "data: [DONE]\n\n"


def _sse_event(payload) -> str:
    if payload == "[DONE]":
        return "data: [DONE]\n\n"
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n"


# ---------------------------------------------------------------------------
# Narrator generation (SSE)
# Autonomous story assistant
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/ai/assistant/conversations")
async def list_assistant_conversations(project_id: str, scope: str = "writer", db: Session = Depends(get_db)):
    """List persisted assistant conversations for a project."""
    get_project_or_404(db, project_id)
    conversations = (
        db.query(AssistantConversation)
        .filter(AssistantConversation.project_id == project_id, AssistantConversation.scope == scope)
        .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.created_at.desc())
        .all()
    )
    items = []
    for conversation in conversations:
        message_count = (
            db.query(AssistantMessage)
            .filter(AssistantMessage.conversation_id == conversation.id)
            .count()
        )
        items.append(_assistant_conversation_to_dict(conversation, message_count))
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.get("/projects/{project_id}/ai/assistant/conversations/{conversation_id}")
async def get_assistant_conversation(
    project_id: str,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """Get one persisted assistant conversation and all messages."""
    conversation = _get_assistant_conversation_or_404(db, project_id, conversation_id)
    messages = (
        db.query(AssistantMessage)
        .filter(AssistantMessage.conversation_id == conversation.id)
        .order_by(
            AssistantMessage.created_at.asc(),
            AssistantMessage.role.desc(),
            AssistantMessage.updated_at.asc(),
            AssistantMessage.id.asc(),
        )
        .all()
    )
    return ApiResponse.success(data={
        "conversation": _assistant_conversation_to_dict(conversation, len(messages)),
        "messages": [_assistant_message_to_dict(message) for message in messages],
    })


@router.delete("/projects/{project_id}/ai/assistant/conversations/{conversation_id}")
async def delete_assistant_conversation(
    project_id: str,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """Delete an assistant conversation."""
    conversation = _get_assistant_conversation_or_404(db, project_id, conversation_id)
    db.delete(conversation)
    db.commit()
    return ApiResponse.success(message="助手对话已删除")




# ---------------------------------------------------------------------------
# Agentic workspace assistant helpers
# ---------------------------------------------------------------------------


def _trim_context_if_needed(messages: list[dict], max_chars: int = 800_000) -> list[dict]:
    total = sum(len(str(m.get("content", ""))) for m in messages)
    if total <= max_chars:
        return messages
    kept = messages[:2]
    recent = messages[-6:] if len(messages) > 6 else messages[2:]
    return kept + recent


@router.post("/projects/{project_id}/ai/workspace-assistant/stream")
async def workspace_assistant_stream(
    project_id: str,
    payload: WorkspaceAssistantRequest,
    db: Session = Depends(get_db),
):
    """Conversational assistant with multi-turn agentic loop — search → reason → act."""
    get_project_or_404(db, project_id)

    async def event_generator():
        conversation = None
        user_msg_db = None
        assistant_msg_db = None
        tool_logs: list[dict] = []
        # Declared at function scope so GeneratorExit recovery can access them
        final_reply = ""
        all_actions: list[dict] = []
        applied_actions: list[dict] = []
        searched_context: list[dict] = []
        final_model = ""
        final_usage = None
        parsed_fallback: dict = {}
        try:
            # --- Phase 1: Setup ---
            selected_node = _find_outline_by_title_or_id(db, project_id, payload.selected_outline_node_id)
            selected_character = (
                _find_character_by_name_or_id(db, project_id, payload.selected_character_id)
                if payload.selected_character_id
                else None
            )
            if payload.conversation_id:
                conversation = _get_assistant_conversation_or_404(db, project_id, payload.conversation_id)
                conversation.scope = payload.scope
            else:
                conversation = AssistantConversation(
                    project_id=project_id,
                    title=_assistant_title_from_message(payload.message),
                    scope=payload.scope,
                )
                db.add(conversation)
                db.flush()
            conversation.current_outline_node_id = selected_node.id if selected_node else None
            conversation.model = payload.model
            conversation.updated_at = datetime.utcnow()

            created_at = datetime.utcnow()
            user_msg_db = AssistantMessage(
                conversation_id=conversation.id,
                role="user",
                content=payload.message,
                status="completed",
                created_at=created_at,
                updated_at=created_at,
            )
            assistant_msg_db = AssistantMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="正在分析需求...",
                status="running",
                payload_json=json.dumps({"tool_logs": []}, ensure_ascii=False),
                created_at=created_at + timedelta(microseconds=1),
                updated_at=created_at + timedelta(microseconds=1),
            )
            db.add(user_msg_db)
            db.add(assistant_msg_db)
            db.commit()
            db.refresh(conversation)
            db.refresh(user_msg_db)
            db.refresh(assistant_msg_db)

            yield _sse_event({
                "type": "conversation",
                "conversation": _assistant_conversation_to_dict(conversation),
                "user_message": _assistant_message_to_dict(user_msg_db),
                "assistant_message": _assistant_message_to_dict(assistant_msg_db),
            })

            # --- Phase 2: Build minimal initial messages ---
            project = get_project_or_404(db, project_id)
            style_context = build_style_context(project, concise=True)
            selected_context: list[str] = []
            if selected_node:
                selected_context.append(f"当前选中大纲：{json.dumps(_outline_node_payload(selected_node), ensure_ascii=False)}")
            if selected_character:
                selected_context.append(f"当前选中角色：{json.dumps(_character_payload(selected_character), ensure_ascii=False)}")
            if payload.selected_text and payload.selected_text.strip():
                chapter_label = ""
                if payload.selected_text_chapter_id:
                    chapter = db.query(Chapter).filter(Chapter.id == payload.selected_text_chapter_id, Chapter.project_id == project_id).first()
                    if chapter:
                        chapter_label = f"，来自章节「{chapter.title}」"
                selected_context.append(f"用户选中了以下文本{chapter_label}：\n```\n{payload.selected_text.strip()}\n```")

            history_text = _assistant_history_from_messages(db, conversation.id, before_message_id=user_msg_db.id, limit=8)
            if history_text == "暂无对话历史。":
                history_text = _assistant_history_text(payload.history)

            previous_search_context = _previous_search_context_from_messages(db, conversation.id, before_message_id=user_msg_db.id)

            system_prompt = build_workspace_assistant_system_prompt(
                scope=payload.scope,
                outline_batch_count=payload.outline_batch_count,
                auto_apply=payload.auto_apply,
            )
            initial_user = build_workspace_assistant_initial_user_message(
                project_title=project.title,
                project_description=project.description,
                style_context=style_context,
                history_text=history_text,
                selected_context=selected_context,
                previous_search_context=previous_search_context,
                outline_batch_count=payload.outline_batch_count,
                auto_apply=payload.auto_apply,
                user_message=payload.message,
            )
            messages: list[dict] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": initial_user},
            ]

            # --- Phase 3: Agentic loop ---
            yield _sse_event({"type": "status", "message": "AI 助手开始分析和检索资料...", "tool": "agent_loop"})

            searched_queries: set[tuple] = set()
            parsed_fallback = {}
            use_function_calling = True

            for iteration in range(1, MAX_ITERATIONS + 1):
                yield _sse_event({
                    "type": "iteration_start",
                    "iteration": iteration,
                    "message": f"第 {iteration}/{MAX_ITERATIONS} 轮推理",
                })

                messages = _trim_context_if_needed(messages)

                if use_function_calling:
                    # --- Function calling path ---
                    content_buffer: list[str] = []
                    tool_call_buffers: dict[int, dict] = {}
                    fc_error = None
                    reasoning_buffer = ""
                    try:
                        stream_gen = LLMGateway.stream_chat_completion_with_tools(
                            messages=messages,
                            model=payload.model,
                            temperature=payload.temperature or 0.3,
                            max_tokens=payload.max_tokens,
                            timeout=300,
                            retry=1,
                            tools=ALL_TOOL_SCHEMAS,
                            tool_choice="auto",
                        )
                        async for chunk in stream_gen:
                            if chunk["type"] == "content_delta":
                                content_buffer.append(chunk["delta"])
                                yield _sse_event({"type": "thinking_delta", "delta": chunk["delta"]})
                            elif chunk["type"] == "reasoning_delta":
                                reasoning_buffer += chunk["delta"]
                            elif chunk["type"] == "tool_call_delta":
                                idx = chunk["index"]
                                if idx not in tool_call_buffers:
                                    tool_call_buffers[idx] = {"id": chunk.get("id", ""), "name": "", "arguments": ""}
                                buf = tool_call_buffers[idx]
                                if chunk.get("id"):
                                    buf["id"] = chunk["id"]
                                if chunk.get("name"):
                                    buf["name"] = chunk["name"]
                                    yield _sse_event({
                                        "type": "tool_call",
                                        "tool": chunk["name"],
                                        "args": {},
                                    })
                                if chunk.get("arguments_delta"):
                                    buf["arguments"] += chunk["arguments_delta"]
                            elif chunk["type"] == "done":
                                if not reasoning_buffer:
                                    reasoning_buffer = chunk.get("reasoning_content", "")
                    except LLMError as e:
                        fc_error = e
                        if "API Key" in str(e) or "提供商" in str(e):
                            raise
                    except Exception as e:
                        fc_error = e

                    if fc_error is not None:
                        use_function_calling = False
                        err_msg = str(fc_error)
                        err_type = type(fc_error).__name__
                        yield _sse_event({
                            "type": "status",
                            "message": f"Function calling 失败（{err_type}: {err_msg}），回退到 JSON 模式。",
                            "tool": "fallback_json",
                        })

                if not use_function_calling:
                    # --- JSON fallback path ---
                    raw_buffer: list[str] = []
                    stream_gen = LLMGateway.stream_chat_completion(
                        messages=messages,
                        model=payload.model,
                        temperature=payload.temperature or 0.3,
                        max_tokens=payload.max_tokens,
                        timeout=300,
                        retry=1,
                    )
                    try:
                        async for chunk in stream_gen:
                            raw_buffer.append(chunk)
                            yield _sse_event({"type": "thinking_delta", "delta": chunk})
                    except Exception as stream_err:
                        yield _sse_event({"type": "status", "message": f"流式输出中断，尝试用已接收内容继续：{stream_err}", "tool": "stream_error"})
                    raw_content = "".join(raw_buffer)
                    parsed = _parse_json_object(raw_content)
                    if parsed is None:
                        yield _sse_event({
                            "type": "status",
                            "message": "模型返回的工具JSON格式不合法，正在自动修复",
                            "tool": "json_repair",
                        })
                        parsed = await _repair_workspace_json_output(raw_content, payload.model)
                        if parsed is not None:
                            tool_logs.append({"tool": "json_repair", "status": "ok", "detail": "已修复模型工具JSON"})
                            yield _sse_event({"type": "tool", **tool_logs[-1]})
                        else:
                            tool_logs.append({"tool": "json_repair", "status": "error", "detail": "模型输出无法解析，未执行写入工具"})
                            yield _sse_event({"type": "tool", **tool_logs[-1]})
                    parsed = parsed or {
                        "reply": "模型返回的工具格式不合法，已停止执行写入。请重试一次，或让助手先生成较短章节。",
                        "done": True,
                        "actions": [],
                        "needs_confirmation": False,
                    }
                    parsed_fallback = parsed
                    final_model = payload.model or ""
                    final_usage = None

                    is_done = bool(parsed.get("done", True))
                    actions: list[dict] = parsed.get("actions") if isinstance(parsed.get("actions"), list) else []
                    reply_part = str(parsed.get("reply") or "")

                    if reply_part:
                        yield _sse_event({"type": "thinking", "content": reply_part, "iteration": iteration})

                    search_actions = [a for a in actions if isinstance(a, dict) and a.get("tool") in SEARCH_TOOL_NAMES]
                    write_actions = [a for a in actions if isinstance(a, dict) and a.get("tool") in WRITE_TOOL_NAMES]

                    if not is_done and write_actions:
                        yield _sse_event({
                            "type": "status",
                            "message": f"跳过 {len(write_actions)} 个写入工具（非最终轮次）",
                            "tool": "skip_write_actions",
                        })
                        write_actions = []

                    yield _sse_event({
                        "type": "tool",
                        "tool": "planner",
                        "status": "ok",
                        "detail": f"第 {iteration} 轮：{'完成分析' if is_done else '需要更多信息'}，{len(search_actions)} 个搜索，{len(write_actions)} 个写入",
                    })

                    if is_done:
                        all_actions = write_actions
                        final_reply = reply_part
                        yield _sse_event({
                            "type": "iteration_end",
                            "iteration": iteration,
                            "message": "分析完成，准备执行最终操作",
                        })
                        break

                    # Execute search actions (JSON path)
                    if search_actions:
                        search_results: list[dict] = []
                        for action in search_actions[:8]:
                            tool_name = str(action.get("tool") or "search")
                            args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}

                            dedup_key = (tool_name, json.dumps(args, ensure_ascii=False, sort_keys=True))
                            if dedup_key in searched_queries:
                                yield _sse_event({
                                    "type": "search_result",
                                    "tool": tool_name,
                                    "result": {"tool": tool_name, "status": "skipped", "detail": "已查询过，见上文结果", "data": []},
                                    "iteration": iteration,
                                })
                                continue
                            searched_queries.add(dedup_key)

                            yield _sse_event({
                                "type": "search_start",
                                "tool": tool_name,
                                "args": args,
                                "iteration": iteration,
                            })
                            try:
                                action_result = await execute_workspace_action(db, project_id, action)
                            except Exception as exc:
                                action_result = {"tool": tool_name, "status": "error", "detail": str(exc), "data": []}
                            search_results.append(action_result)
                            tool_logs.append({
                                "tool": action_result.get("tool") or tool_name,
                                "status": action_result.get("status") or "ok",
                                "detail": action_result.get("detail") or "",
                            })
                            yield _sse_event({
                                "type": "search_result",
                                "tool": tool_name,
                                "result": action_result,
                                "iteration": iteration,
                            })

                        for action_result in search_results:
                            compressed = _compress_search_result(action_result)
                            if compressed:
                                searched_context.append(compressed)

                        messages.append({"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)})
                        messages.append({"role": "user", "content": format_tool_result_message(iteration, search_results)})

                    yield _sse_event({
                        "type": "iteration_end",
                        "iteration": iteration,
                        "message": f"第 {iteration} 轮完成，{'获得 ' + str(len(search_actions)) + ' 条搜索结果' if search_actions else '未请求搜索'}",
                    })

                    if iteration == MAX_ITERATIONS:
                        yield _sse_event({
                            "type": "status",
                            "message": f"已达到 {MAX_ITERATIONS} 轮搜索上限，基于已有信息给出最终回复",
                            "tool": "max_iterations",
                        })
                        all_actions = []
                        final_reply = parsed.get("reply", "") or "已分析完毕。"
                        break
                    continue

                # --- Function calling: process accumulated tool calls ---
                reply_text = "".join(content_buffer)
                if reply_text:
                    yield _sse_event({"type": "thinking", "content": reply_text, "iteration": iteration})

                # Build tool_calls list from accumulated buffers
                tool_calls: list[dict] = []
                for idx in sorted(tool_call_buffers.keys()):
                    buf = tool_call_buffers[idx]
                    if not buf["name"]:
                        continue
                    try:
                        args = json.loads(buf["arguments"]) if buf["arguments"].strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append({
                        "id": buf["id"],
                        "type": "function",
                        "function": {
                            "name": buf["name"],
                            "arguments": json.dumps(args, ensure_ascii=False),
                        },
                    })

                se_names = SEARCH_TOOL_NAMES
                wr_names = WRITE_TOOL_NAMES

                yield _sse_event({
                    "type": "tool",
                    "tool": "planner",
                    "status": "ok",
                    "detail": f"第 {iteration} 轮：{len(tool_calls)} 个工具调用（{len([t for t in tool_calls if t['function']['name'] in se_names])} 个搜索，{len([t for t in tool_calls if t['function']['name'] in wr_names])} 个写入）",
                })

                # Agent decides it's done — no tool calls, just text
                if not tool_calls:
                    if iteration <= 2 and reply_text.strip():
                        # Guard: agent stopped too early with a text reply
                        _asst_msg: dict = {"role": "assistant", "content": reply_text}
                        if reasoning_buffer:
                            _asst_msg["reasoning_content"] = reasoning_buffer
                        messages.append(_asst_msg)
                        messages.append({"role": "user", "content": "信息还不足够，请继续搜索。先查相关章节正文和大纲上下文，不要急于给出最终回复。"})
                        yield _sse_event({
                            "type": "iteration_end",
                            "iteration": iteration,
                            "message": "信息不足，要求继续搜索",
                        })
                        continue
                    # Agent is truly done
                    final_reply = reply_text
                    final_model = payload.model or ""
                    final_usage = None
                    yield _sse_event({
                        "type": "iteration_end",
                        "iteration": iteration,
                        "message": "Agent 判断任务完成",
                    })
                    break

                # Agent called tools — execute ALL of them (search and write alike)
                all_results: list[dict] = []
                for tc in tool_calls[:12]:
                    tool_name = tc["function"]["name"]
                    try:
                        tc_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        tc_args = {}

                    dedup_key = (tool_name, json.dumps(tc_args, ensure_ascii=False, sort_keys=True))
                    is_write = tool_name in wr_names
                    action_type = "write" if is_write else "search"
                    if tool_name in se_names and dedup_key in searched_queries:
                        yield _sse_event({
                            "type": "search_result",
                            "tool": tool_name,
                            "result": {"tool": tool_name, "status": "skipped", "detail": "已查询过，见上文结果", "data": []},
                            "iteration": iteration,
                        })
                        all_results.append({"tool": tool_name, "status": "skipped", "detail": "已查询过", "data": []})
                        continue
                    searched_queries.add(dedup_key)

                    yield _sse_event({
                        "type": f"{action_type}_start",
                        "tool": tool_name,
                        "args": tc_args,
                        "iteration": iteration,
                    })

                    action = {"tool": tool_name, "arguments": tc_args}
                    try:
                        action_result = await execute_workspace_action(db, project_id, action)
                    except Exception as exc:
                        action_result = {"tool": tool_name, "status": "error", "detail": str(exc), "data": []}

                    all_results.append(action_result)
                    tool_logs.append({
                        "tool": action_result.get("tool") or tool_name,
                        "status": action_result.get("status") or "ok",
                        "detail": action_result.get("detail") or "",
                    })
                    yield _sse_event({
                        "type": f"{action_type}_result",
                        "tool": tool_name,
                        "result": action_result,
                        "iteration": iteration,
                    })

                for action_result in all_results:
                    if action_result.get("tool") in se_names:
                        compressed = _compress_search_result(action_result)
                        if compressed:
                            searched_context.append(compressed)

                # Feed results back as tool_result messages
                assistant_tool_calls = [
                    {"id": tc["id"], "type": "function", "function": tc["function"]}
                    for tc in tool_calls
                ]
                _asst_msg = {
                    "role": "assistant",
                    "content": reply_text or None,
                    "tool_calls": assistant_tool_calls,
                }
                if reasoning_buffer:
                    _asst_msg["reasoning_content"] = reasoning_buffer
                messages.append(_asst_msg)
                for tc, ar in zip(tool_calls, all_results):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(ar, ensure_ascii=False),
                    })

                yield _sse_event({
                    "type": "iteration_end",
                    "iteration": iteration,
                    "message": f"第 {iteration} 轮完成，执行了 {len(tool_calls)} 个工具",
                })
                # No continue here — loop naturally goes to next iteration
            else:
                # Loop completed without break (shouldn't happen, but guard)
                all_actions = []
                final_reply = "已分析完毕。"

            # --- Phase 4: Final write action execution ---
            needs_conf = False
            if all_actions and _chapter_action_needs_outline_confirmation(db, project_id, all_actions, payload.message):
                all_actions = []
                needs_conf = True
                reply = final_reply.strip()
                if "是否" not in reply and "确认" not in reply:
                    final_reply = (
                        f"{reply}\n\n" if reply else ""
                    ) + f"我会先按接下来 {payload.outline_batch_count} 章给出大纲方向，请你确认是否按这个方向发展。"

            if payload.auto_apply and all_actions:
                created_outline_ids_by_title: dict[str, str] = {}
                for action in all_actions[:12]:
                    tool = str(action.get("tool") or "tool")
                    args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
                    if tool in {"create_chapter", "update_chapter"}:
                        outline_title = str(args.get("outline_node_title") or args.get("outline_title") or "").strip()
                        if outline_title and outline_title in created_outline_ids_by_title:
                            args["outline_node_id"] = created_outline_ids_by_title[outline_title]
                            action["arguments"] = args
                    yield _sse_event({"type": "status", "message": f"正在执行工具：{tool}", "tool": tool})
                    try:
                        action_result = await _execute_workspace_action(db, project_id, action)
                    except Exception as exc:
                        action_result = {"tool": tool, "status": "error", "detail": str(exc)}
                    if tool == "create_outline_node" and action_result.get("status") == "ok":
                        data = action_result.get("data") if isinstance(action_result.get("data"), dict) else {}
                        title = str(data.get("title") or args.get("title") or "").strip()
                        node_id = str(data.get("id") or "").strip()
                        if title and node_id:
                            created_outline_ids_by_title[title] = node_id
                    applied_actions.append(action_result)
                    tool_logs.append({
                        "tool": action_result.get("tool") or tool,
                        "status": action_result.get("status") or "ok",
                        "detail": action_result.get("detail") or "",
                    })
                    yield _sse_event({"type": "tool", **tool_logs[-1]})
                db.commit()

                # Auto-refresh search context so next turn sees fresh data
                refresh_tools: dict[str, str] = {}
                for ar in applied_actions:
                    tool = str(ar.get("tool") or "")
                    if ar.get("status") != "ok":
                        continue
                    if tool in ("create_outline_node", "update_outline_node", "delete_outline_node"):
                        refresh_tools["search_outline_tree"] = "{}"
                    elif tool in ("create_character", "update_character", "delete_character"):
                        refresh_tools["list_characters"] = "{}"
                    elif tool in ("create_worldbuilding_entry", "update_worldbuilding_entry", "delete_worldbuilding_entry"):
                        refresh_tools["list_worldbuilding"] = "{}"
                    elif tool in ("create_chapter", "update_chapter", "delete_chapter"):
                        refresh_tools["list_chapters"] = "{}"
                for rt, rt_args in refresh_tools.items():
                    try:
                        rt_result = await execute_workspace_action(db, project_id, {"tool": rt, "arguments": json.loads(rt_args)})
                        compressed = _compress_search_result(rt_result)
                        if compressed:
                            searched_context.append(compressed)
                    except Exception:
                        pass
            elif all_actions:
                log = {"tool": "auto_apply", "status": "skipped", "detail": "自动执行已关闭"}
                tool_logs.append(log)
                yield _sse_event({"type": "tool", **log})

            # --- Phase 5: Finalize ---
            response_payload = {
                "reply": final_reply or "已完成。",
                "actions": all_actions,
                "applied_actions": applied_actions,
                "tool_logs": tool_logs,
                "searched_context": searched_context,
                "scope": payload.scope,
                "model": final_model,
                "usage": final_usage,
            }
            assistant_msg_db.content = response_payload["reply"]
            assistant_msg_db.payload_json = json.dumps(response_payload, ensure_ascii=False)
            assistant_msg_db.status = "completed"
            assistant_msg_db.updated_at = datetime.utcnow()
            conversation.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(assistant_msg_db)
            db.refresh(conversation)
            response_payload["message"] = _assistant_message_to_dict(assistant_msg_db)
            response_payload["conversation"] = _assistant_conversation_to_dict(conversation)
            yield _sse_event({"type": "complete", "data": response_payload})
            yield _sse_event("[DONE]")
        except (GeneratorExit, asyncio.CancelledError):
            # Client disconnected during streaming — finish critical work silently
            if all_actions and payload.auto_apply:
                for action in all_actions[:12]:
                    tool = str(action.get("tool") or "tool")
                    try:
                        action_result = await _execute_workspace_action(db, project_id, action)
                    except Exception:
                        action_result = {"tool": tool, "status": "error", "detail": "后台执行失败"}
                    applied_actions.append(action_result)
                db.commit()
            if assistant_msg_db:
                reply = final_reply or parsed_fallback.get("reply", "") or "已分析完毕。"
                assistant_msg_db.content = reply
                assistant_msg_db.payload_json = json.dumps({
                    "reply": reply,
                    "actions": all_actions,
                    "applied_actions": applied_actions,
                    "tool_logs": tool_logs,
                    "searched_context": searched_context,
                    "scope": payload.scope,
                    "model": final_model,
                    "usage": final_usage,
                }, ensure_ascii=False)
                assistant_msg_db.status = "completed"
                assistant_msg_db.updated_at = datetime.utcnow()
                if conversation:
                    conversation.updated_at = datetime.utcnow()
                db.commit()
        except LLMError as exc:
            if assistant_msg_db:
                assistant_msg_db.content = str(exc)
                assistant_msg_db.status = "error"
                assistant_msg_db.payload_json = json.dumps({"tool_logs": tool_logs}, ensure_ascii=False)
                db.commit()
            yield _sse_event({"type": "error", "message": str(exc)})
            yield _sse_event("[DONE]")
        except Exception as exc:
            if assistant_msg_db:
                assistant_msg_db.content = f"服务器错误: {exc}"
                assistant_msg_db.status = "error"
                assistant_msg_db.payload_json = json.dumps({"tool_logs": tool_logs}, ensure_ascii=False)
                db.commit()
            yield _sse_event({"type": "error", "message": f"服务器错误: {exc}"})
            yield _sse_event("[DONE]")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
