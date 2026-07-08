"""Bridge between user intent detection and AgentPlan execution.

Connects the main assistant chat flow with the plan orchestrator:
detect_intent -> build_plan -> create_plan -> execute_plan (SSE events).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...core.db_helpers import get_project_or_404
from ...database.models import (
    AgentPlanStep,
    AssistantMemory,
    AssistantConversation,
    AssistantMessage,
    OutlineNode,
)
from ...prompts.workspace_assistant import format_memory_context
from ..skills.service import build_skill_prompt_section, select_relevant_skills
from ..workspace.registry import registry
from ..workspace.run_log import (
    create_assistant_run,
    mark_assistant_run,
    run_payload,
)
from .orchestrator import PlanOrchestrator, _serialize_step
from .plan_graph import PlanGraph
from .planner import build_plan_from_intent, detect_intent


def _apply_assistant_mode_to_intent(
    intent: dict[str, Any],
    assistant_mode: str | None,
) -> dict[str, Any]:
    """Let the frontend assistant mode choose the chapter plan variant."""
    if intent.get("intent_type") == "chapter" and (assistant_mode or "").lower() == "quality":
        return {**intent, "mode": "quality"}
    return intent


def _sse_event(payload: Any) -> str:
    if payload == "[DONE]":
        return "data: [DONE]\n\n"
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n"


def _resolve_outline_node_id(
    db: Session,
    project_id: str,
    chapter_number: int | None,
    outline_query: str,
) -> str:
    """Try to find an outline node ID from chapter number or query text."""
    if chapter_number is not None:
        node = (
            db.query(OutlineNode)
            .filter(
                OutlineNode.project_id == project_id,
                OutlineNode.title.contains(str(chapter_number)),
            )
            .first()
        )
        if node:
            return node.id

    # Try matching by keywords from the query
    import re
    match = re.search(r"第\s*(\d+)\s*章", outline_query or "")
    if match:
        num = match.group(1)
        node = (
            db.query(OutlineNode)
            .filter(
                OutlineNode.project_id == project_id,
                OutlineNode.title.contains(num),
            )
            .first()
        )
        if node:
            return node.id

    return ""


def _latest_outline_label(db: Session, project_id: str) -> str:
    node = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.sort_order.desc(), OutlineNode.created_at.desc())
        .first()
    )
    if not node:
        return "当前项目还没有大纲节点"
    return f"当前最新大纲是「{node.title}」"


def _latest_outline_chapter_number(db: Session, project_id: str) -> int | None:
    nodes = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .all()
    )
    numbers: list[int] = []
    for node in nodes:
        match = re.search(r"第\s*(\d+)\s*章", node.title or "")
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers) if numbers else None


def _infer_outline_chapter_number(
    db: Session,
    project_id: str,
    conversation_id: str | None,
) -> int | None:
    if conversation_id:
        messages = (
            db.query(AssistantMessage)
            .join(AssistantConversation, AssistantConversation.id == AssistantMessage.conversation_id)
            .filter(
                AssistantConversation.project_id == project_id,
                AssistantMessage.conversation_id == conversation_id,
            )
            .order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc())
            .limit(8)
            .all()
        )
        for message in messages:
            text = message.content or ""
            match = re.search(r"未找到第\s*(\d+)\s*章的大纲节点", text)
            if match:
                return int(match.group(1))
            match = re.search(r"请先创建第\s*(\d+)\s*章大纲", text)
            if match:
                return int(match.group(1))
    latest = _latest_outline_chapter_number(db, project_id)
    return latest + 1 if latest else None


def _enrich_outline_intent(
    db: Session,
    project_id: str,
    intent: dict[str, Any],
    *,
    conversation_id: str | None,
    outline_batch_count: int,
) -> dict[str, Any]:
    if intent.get("intent_type") != "outline":
        return intent
    enriched = dict(intent)
    chapter_number = enriched.get("chapter_number") or _infer_outline_chapter_number(db, project_id, conversation_id)
    if chapter_number:
        enriched["chapter_number"] = chapter_number
    batch_count = enriched.get("batch_count")
    if not batch_count:
        message = str(enriched.get("requirements") or "")
        wants_batch = any(key in message for key in ("后续", "连续", "接下来", "往后", "一批"))
        batch_count = outline_batch_count if wants_batch else 1
    enriched["batch_count"] = max(1, min(8, int(batch_count or 1)))

    requirements = str(enriched.get("requirements") or "").strip()
    target = ""
    if chapter_number and enriched["batch_count"] == 1:
        target = f"目标：创建第 {chapter_number} 章大纲，承接当前最新大纲，不要生成正文。"
    elif chapter_number:
        end_number = chapter_number + enriched["batch_count"] - 1
        target = f"目标：创建第 {chapter_number} 章至第 {end_number} 章的大纲，承接当前最新大纲，不要生成正文。"
    if target and target not in requirements:
        requirements = "\n".join(part for part in (requirements, target) if part)
    enriched["requirements"] = requirements
    return enriched


def _model_provider(model: str | None) -> str:
    try:
        from ...ai.gateway import LLMGateway

        return LLMGateway.provider_for_model(model)
    except Exception:
        return (model or "").split(":", 1)[0].strip().lower()


def _stream_assistant_notice(
    db: Session,
    project_id: str,
    *,
    message: str,
    reply: str,
    conversation_id: str | None = None,
    scope: str = "project",
    model: str | None = None,
    assistant_mode: str = "fast",
    skill_info: list[dict] | None = None,
    tool_detail: str = "计划前置检查未通过",
) -> AsyncGenerator[str, None]:
    async def _stream() -> AsyncGenerator[str, None]:
        created_at = datetime.utcnow()
        if conversation_id:
            conversation = db.query(AssistantConversation).filter(
                AssistantConversation.id == conversation_id,
                AssistantConversation.project_id == project_id,
            ).first()
            if not conversation:
                conversation = AssistantConversation(
                    project_id=project_id,
                    title=_assistant_title_from_message(message),
                    scope=scope,
                )
                db.add(conversation)
                db.flush()
            conversation.scope = scope
            conversation.model = model
        else:
            conversation = AssistantConversation(
                project_id=project_id,
                title=_assistant_title_from_message(message),
                scope=scope,
                model=model,
            )
            db.add(conversation)
            db.flush()
        conversation.updated_at = created_at

        user_msg_db = AssistantMessage(
            conversation_id=conversation.id,
            role="user",
            content=message,
            status="completed",
            created_at=created_at,
            updated_at=created_at,
        )
        assistant_msg_db = AssistantMessage(
            conversation_id=conversation.id,
            role="assistant",
            content="正在检查执行条件...",
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

        assistant_run = create_assistant_run(
            db,
            project_id=project_id,
            conversation_id=conversation.id,
            user_message_id=user_msg_db.id,
            assistant_message_id=assistant_msg_db.id,
            scope=scope,
            assistant_mode=assistant_mode,
            model=model,
        )

        yield _sse_event({
            "type": "conversation",
            "conversation": _assistant_conversation_to_dict(conversation),
            "user_message": _assistant_message_to_dict(user_msg_db),
            "assistant_message": _assistant_message_to_dict(assistant_msg_db),
        })
        yield _sse_event({"type": "run", "run": run_payload(assistant_run)})
        if skill_info:
            yield _sse_event({"type": "skills_matched", "skills": skill_info})

        tool_logs = [{"tool": "plan_preflight", "status": "skipped", "detail": tool_detail}]
        yield _sse_event({"type": "tool", **tool_logs[0]})
        response_payload = {
            "reply": reply,
            "actions": [],
            "applied_actions": [],
            "tool_logs": tool_logs,
            "searched_context": [],
            "scope": scope,
            "model": model or "",
            "usage": None,
            "skills": skill_info,
        }
        assistant_msg_db.content = reply
        assistant_msg_db.payload_json = json.dumps(response_payload, ensure_ascii=False)
        assistant_msg_db.status = "completed"
        assistant_msg_db.updated_at = datetime.utcnow()
        conversation.updated_at = datetime.utcnow()
        db.commit()

        mark_assistant_run(
            db,
            assistant_run,
            status="completed",
            phase="plan_preflight",
            final_reply=reply,
        )
        db.refresh(assistant_run)
        db.refresh(assistant_msg_db)
        db.refresh(conversation)
        response_payload["run"] = run_payload(assistant_run)
        response_payload["message"] = _assistant_message_to_dict(assistant_msg_db)
        response_payload["conversation"] = _assistant_conversation_to_dict(conversation)
        yield _sse_event({"type": "complete", "data": response_payload})
        yield _sse_event("[DONE]")

    return _stream()


def _assistant_title_from_message(message: str) -> str:
    title = " ".join((message or "").strip().split())
    if not title:
        return "新对话"
    return title[:36] + ("..." if len(title) > 36 else "")


def _assistant_conversation_to_dict(conversation: AssistantConversation, message_count: int | None = None) -> dict:
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


def _build_memory_context(db: Session, project_id: str, message: str) -> str:
    """Recall stable preferences plus message-related memories for plan runs."""
    fixed_categories = ["user_preference", "writing_style", "workflow_preference", "preference"]
    related_categories = ["project_fact", "research_note", "fact", "search_result", "note"]

    fixed_memories = (
        db.query(AssistantMemory)
        .filter(
            AssistantMemory.project_id == project_id,
            AssistantMemory.category.in_(fixed_categories),
        )
        .order_by(AssistantMemory.importance.desc(), AssistantMemory.updated_at.desc())
        .limit(10)
        .all()
    )

    related_memories: list[AssistantMemory] = []
    raw_terms = re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9_-]{2,30}", message or "")
    query_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in raw_terms[:5]:
        candidates = [term]
        if re.fullmatch(r"[\u4e00-\u9fff]{3,12}", term):
            for size in (2, 3, 4):
                candidates.extend(term[i:i + size] for i in range(0, max(len(term) - size + 1, 0)))
        for candidate in candidates:
            if candidate not in seen_terms:
                seen_terms.add(candidate)
                query_terms.append(candidate)
            if len(query_terms) >= 24:
                break
        if len(query_terms) >= 24:
            break
    if query_terms:
        query = db.query(AssistantMemory).filter(
            AssistantMemory.project_id == project_id,
            AssistantMemory.category.in_(related_categories),
        )
        term_filters = []
        for term in query_terms:
            term_filters.append(AssistantMemory.key.ilike(f"%{term}%") | AssistantMemory.value.ilike(f"%{term}%"))
        if term_filters:
            query = query.filter(or_(*term_filters))
        related_memories = query.order_by(AssistantMemory.importance.desc()).limit(10).all()

    seen_ids = {memory.id for memory in fixed_memories}
    memories = [
        {
            "category": memory.category,
            "key": memory.key,
            "value": memory.value,
            "importance": memory.importance,
        }
        for memory in fixed_memories
    ]
    memories.extend(
        {
            "category": memory.category,
            "key": memory.key,
            "value": memory.value,
            "importance": memory.importance,
        }
        for memory in related_memories
        if memory.id not in seen_ids
    )
    return format_memory_context(memories)


def _inject_memory_into_intent(intent: dict[str, Any], memory_context: str) -> dict[str, Any]:
    """Attach recalled memory to plan requirements without changing search keys."""
    if not memory_context.strip():
        return intent
    enriched = dict(intent)
    requirements = str(enriched.get("requirements") or "").strip()
    enriched["requirements"] = "\n\n".join(part for part in (requirements, memory_context.strip()) if part)
    return enriched


def _inject_skill_prompts_into_intent(intent: dict[str, Any], skill_prompts: str) -> dict[str, Any]:
    """Attach matched skill instructions to plan tool requirements.

    Plan execution calls workspace tools directly, so it does not pass through
    the old assistant system prompt where skills are normally injected.
    Adding the selected skill section to requirements gives generator tools the
    same project-specific behavior without changing the registry/tool contract.
    """
    if not skill_prompts.strip():
        return intent
    enriched = dict(intent)
    requirements = str(enriched.get("requirements") or "").strip()
    skill_block = f"[Matched skill instructions]\n{skill_prompts.strip()}"
    enriched["requirements"] = "\n\n".join(part for part in (requirements, skill_block) if part)
    return enriched


def _schedule_memory_extraction(project_id: str, user_message: str, assistant_reply: str, model: str | None) -> None:
    """Best-effort memory extraction after a plan completes."""
    if not user_message.strip() or not assistant_reply.strip():
        return
    try:
        from ...ai.gateway import LLMGateway

        if not LLMGateway.supports_tool_calling(model):
            return
    except Exception:
        # Keep memory extraction best-effort: missing model config should not
        # block the assistant, and the extraction task already handles it.
        pass

    async def _extract_and_save_memories() -> None:
        import logging

        from ...ai.gateway import LLMGateway
        from ...database.session import SessionLocal
        from ...prompts.packs.memory_extraction import PACK
        from ..workspace.tools.memory import remember

        db = SessionLocal()
        try:
            conversation = f"用户：{user_message}\n助手：{assistant_reply}"
            response = await LLMGateway.chat_completion(
                messages=[
                    {"role": "system", "content": PACK.build_system_prompt()},
                    {"role": "user", "content": conversation},
                ],
                model=model,
                temperature=0.2,
                max_tokens=2000,
            )
            raw = response.get("content", "")
            try:
                items = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                match = re.search(r"\[.*\]", raw, re.DOTALL)
                items = json.loads(match.group()) if match else []
            if not isinstance(items, list):
                return

            saved = 0
            for item in items:
                if saved >= 5 or not isinstance(item, dict):
                    break
                key = str(item.get("key") or "").strip()
                value = str(item.get("value") or "").strip()
                evidence = str(item.get("evidence") or "").strip()
                category = str(item.get("category") or "").strip()
                importance = int(item.get("importance") or 0)
                if not key or not value or not evidence or importance < 7 or evidence not in user_message:
                    continue
                await remember(db, project_id, {
                    "key": key,
                    "value": value,
                    "category": category,
                    "importance": importance,
                    "source": "auto_extract",
                })
                saved += 1
        except Exception:
            logging.getLogger(__name__).debug("plan memory auto-extract skipped", exc_info=True)
        finally:
            db.close()

    import asyncio

    asyncio.create_task(_extract_and_save_memories())


def _stream_cataloging_job(
    db: Session,
    project_id: str,
    *,
    message: str,
    conversation_id: str | None = None,
    scope: str = "project",
    model: str | None = None,
    skill_info: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream a cataloging job as SSE events for the assistant chat.

    This handles the 'project_init' intent by creating a cataloging job
    and streaming its progress directly.
    """
    from ..cataloging.orchestrator import create_cataloging_job, stream_cataloging_job

    async def _stream() -> AsyncGenerator[str, None]:
        created_at = datetime.utcnow()

        # Create or reuse conversation
        if conversation_id:
            conversation = db.query(AssistantConversation).filter(
                AssistantConversation.id == conversation_id,
                AssistantConversation.project_id == project_id,
            ).first()
            if not conversation:
                conversation = AssistantConversation(
                    project_id=project_id,
                    title=_assistant_title_from_message(message),
                    scope=scope,
                )
                db.add(conversation)
                db.flush()
            conversation.scope = scope
            conversation.model = model
        else:
            conversation = AssistantConversation(
                project_id=project_id,
                title=_assistant_title_from_message(message),
                scope=scope,
                model=model,
            )
            db.add(conversation)
            db.flush()

        conversation.updated_at = created_at

        user_msg_db = AssistantMessage(
            conversation_id=conversation.id,
            role="user",
            content=message,
            status="completed",
            created_at=created_at,
            updated_at=created_at,
        )
        assistant_msg_db = AssistantMessage(
            conversation_id=conversation.id,
            role="assistant",
            content="正在创建作品建档任务...",
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

        assistant_run = create_assistant_run(
            db,
            project_id=project_id,
            conversation_id=conversation.id,
            user_message_id=user_msg_db.id,
            assistant_message_id=assistant_msg_db.id,
            scope=scope,
            model=model,
        )

        # Yield conversation and run info
        yield _sse_event({
            "type": "conversation",
            "conversation": _assistant_conversation_to_dict(conversation),
            "user_message": _assistant_message_to_dict(user_msg_db),
            "assistant_message": _assistant_message_to_dict(assistant_msg_db),
        })
        yield _sse_event({"type": "run", "run": run_payload(assistant_run)})
        if skill_info:
            yield _sse_event({"type": "skills_matched", "skills": skill_info})

        # Create cataloging job
        job = create_cataloging_job(
            db, project_id,
            execution_mode="auto",
            model=model,
            chapter_ids=None,  # All chapters
        )

        yield _sse_event({
            "type": "status",
            "message": f"建档任务已创建，共 {job.total_chapters} 章",
            "tool": "cataloging",
        })

        # Stream cataloging job progress
        tool_logs: list[dict] = []
        try:
            async for event_str in stream_cataloging_job(project_id, job.id):
                # Forward cataloging SSE events as plan-style events
                if event_str.startswith("data: "):
                    data_str = event_str[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        event_data = json.loads(data_str)
                        event_type = event_data.get("type", "")

                        # Map cataloging events to plan-style events
                        if event_type == "chapter_started":
                            yield _sse_event({
                                "type": "status",
                                "message": f"正在处理：{event_data.get('run', {}).get('chapter_title', '')}",
                                "tool": "cataloging",
                            })
                        elif event_type == "fact_extracted":
                            yield _sse_event({
                                "type": "tool",
                                "tool": "extract_facts",
                                "status": "ok",
                                "detail": event_data.get("fact", {}).get("summary", "事实已提取"),
                            })
                        elif event_type == "candidate_created":
                            yield _sse_event({
                                "type": "tool",
                                "tool": "resolve_targets",
                                "status": "ok",
                                "detail": event_data.get("candidate", {}).get("item_type", "候选已生成"),
                            })
                        elif event_type == "chapter_completed":
                            run_data = event_data.get("run", {})
                            tool_logs.append({
                                "tool": "cataloging",
                                "status": "ok",
                                "detail": f"章节完成：{run_data.get('chapter_title', '')}",
                            })
                            yield _sse_event({
                                "type": "step_result",
                                "step_key": f"chapter_{run_data.get('chapter_id', '')}",
                                "tool": "cataloging",
                                "status": "ok",
                                "detail": f"章节完成：{run_data.get('chapter_title', '')}",
                            })
                        elif event_type == "chapter_failed":
                            run_data = event_data.get("run", {})
                            tool_logs.append({
                                "tool": "cataloging",
                                "status": "error",
                                "detail": f"章节失败：{event_data.get('error', '')}",
                            })
                            yield _sse_event({
                                "type": "step_result",
                                "step_key": f"chapter_{run_data.get('chapter_id', '')}",
                                "tool": "cataloging",
                                "status": "error",
                                "detail": event_data.get("error", "处理失败"),
                            })
                        elif event_type == "completed":
                            yield _sse_event({
                                "type": "plan_end",
                                "status": "completed",
                            })
                        elif event_type == "job":
                            # Job status update
                            job_data = event_data.get("job", {})
                            yield _sse_event({
                                "type": "status",
                                "message": f"进度：{job_data.get('completed_chapters', 0)}/{job_data.get('total_chapters', 0)} 章",
                                "tool": "cataloging",
                            })
                    except json.JSONDecodeError:
                        pass
        except Exception as exc:
            from ...core.exceptions import LLMError, NotFoundError
            if isinstance(exc, (LLMError, NotFoundError)) or "API" in str(exc) or "Key" in str(exc) or "credit" in str(exc).lower() or "quota" in str(exc).lower() or "欠费" in str(exc) or "额度" in str(exc):
                yield _sse_event({
                    "type": "error",
                    "message": f"建档任务异常: {exc}\n\n提示：司命模型 API 不可用。您可以用 Claude Code 或 Codex 直接进行外部编目（无需司命 API）。请发送「用 Claude 建档」或「外部编目」启动。",
                })
            else:
                yield _sse_event({"type": "error", "message": f"建档任务异常: {exc}"})

        # Finalize
        final_reply = f"作品建档任务已完成。共处理 {job.total_chapters} 个章节。"

        response_payload = {
            "reply": final_reply,
            "actions": [],
            "applied_actions": [],
            "tool_logs": tool_logs,
            "scope": scope,
            "model": model or "",
            "usage": None,
            "skills": skill_info,
        }

        assistant_msg_db.content = final_reply
        assistant_msg_db.payload_json = json.dumps(response_payload, ensure_ascii=False)
        assistant_msg_db.status = "completed"
        assistant_msg_db.updated_at = datetime.utcnow()
        conversation.updated_at = datetime.utcnow()
        db.commit()

        mark_assistant_run(
            db, assistant_run,
            status="completed",
            phase="cataloging_completed",
            final_reply=final_reply,
        )
        db.refresh(assistant_run)
        db.refresh(assistant_msg_db)
        db.refresh(conversation)

        response_payload["run"] = run_payload(assistant_run)
        response_payload["message"] = _assistant_message_to_dict(assistant_msg_db)
        response_payload["conversation"] = _assistant_conversation_to_dict(conversation)
        yield _sse_event({"type": "complete", "data": response_payload})
        _schedule_memory_extraction(project_id, message, final_reply, model)
        yield _sse_event("[DONE]")

    return _stream()


async def detect_and_stream_plan(
    db: Session,
    project_id: str,
    *,
    message: str,
    conversation_id: str | None = None,
    scope: str = "project",
    model: str | None = None,
    assistant_mode: str = "fast",
    outline_batch_count: int = 1,
) -> AsyncGenerator[str, None] | None:
    """Detect intent and stream plan execution via SSE.

    Returns an SSE event generator if a plan intent was detected, or None
    if no plan intent was found (caller should fall back to old agentic loop).
    """
    intent = detect_intent(message)
    if intent is None:
        return None
    intent = _apply_assistant_mode_to_intent(intent, assistant_mode)
    intent = _enrich_outline_intent(
        db,
        project_id,
        intent,
        conversation_id=conversation_id,
        outline_batch_count=outline_batch_count,
    )

    # For chapter plans, we need an outline_node_id
    outline_node_id = ""
    if intent.get("intent_type") == "chapter":
        outline_node_id = _resolve_outline_node_id(
            db, project_id,
            intent.get("chapter_number"),
            intent.get("outline_query", ""),
        )

    memory_context = _build_memory_context(db, project_id, message)
    matched_skills = select_relevant_skills(db, project_id, message, scope)
    skill_prompt_section, skill_info = build_skill_prompt_section(matched_skills)
    intent = _inject_skill_prompts_into_intent(intent, skill_prompt_section)
    intent = _inject_memory_into_intent(intent, memory_context)

    if intent.get("intent_type") == "chapter":
        issues: list[str] = []
        chapter_number = intent.get("chapter_number")
        if not outline_node_id:
            latest = _latest_outline_label(db, project_id)
            if chapter_number:
                issues.append(
                    f"未找到第 {chapter_number} 章的大纲节点。{latest}。"
                    f"请先创建第 {chapter_number} 章大纲，或明确说明要先规划下一章再写。"
                )
            else:
                issues.append("没有定位到要写的章节大纲节点，请指定已有大纲标题或章节号。")
        if _model_provider(model) == "local_llama_cpp":
            issues.append(
                "当前选择的是司命本地 AI（本地文本模型），不再执行内部整章生成器 chapter_writer。"
                "请切换到 API 或本机 CLI 模型后再写整章，或使用外部写作流程。"
            )
        if issues:
            reply = "暂时不能执行写章计划：\n" + "\n".join(f"- {issue}" for issue in issues)
            return _stream_assistant_notice(
                db,
                project_id,
                message=message,
                reply=reply,
                conversation_id=conversation_id,
                scope=scope,
                model=model,
                assistant_mode=assistant_mode,
                skill_info=skill_info,
                tool_detail="；".join(issues),
            )

    # Handle project_init (cataloging) intent directly — don't use plan system
    if intent.get("intent_type") == "project_init":
        return _stream_cataloging_job(
            db, project_id,
            message=message,
            conversation_id=conversation_id,
            scope=scope,
            model=model,
            skill_info=skill_info,
        )

    graph = build_plan_from_intent(intent, outline_node_id=outline_node_id)
    if graph is None:
        return None

    async def _stream() -> AsyncGenerator[str, None]:
        # --- Setup conversation, messages, run ---
        created_at = datetime.utcnow()

        if conversation_id:
            conversation = db.query(AssistantConversation).filter(
                AssistantConversation.id == conversation_id,
                AssistantConversation.project_id == project_id,
            ).first()
            if not conversation:
                conversation = AssistantConversation(
                    project_id=project_id,
                    title=_assistant_title_from_message(message),
                    scope=scope,
                )
                db.add(conversation)
                db.flush()
            conversation.scope = scope
            conversation.model = model
        else:
            conversation = AssistantConversation(
                project_id=project_id,
                title=_assistant_title_from_message(message),
                scope=scope,
                model=model,
            )
            db.add(conversation)
            db.flush()

        conversation.updated_at = created_at

        user_msg_db = AssistantMessage(
            conversation_id=conversation.id,
            role="user",
            content=message,
            status="completed",
            created_at=created_at,
            updated_at=created_at,
        )
        assistant_msg_db = AssistantMessage(
            conversation_id=conversation.id,
            role="assistant",
            content="正在创建执行计划...",
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

        assistant_run = create_assistant_run(
            db,
            project_id=project_id,
            conversation_id=conversation.id,
            user_message_id=user_msg_db.id,
            assistant_message_id=assistant_msg_db.id,
            scope=scope,
            assistant_mode=assistant_mode,
            model=model,
        )

        # Yield conversation and run info
        yield _sse_event({
            "type": "conversation",
            "conversation": _assistant_conversation_to_dict(conversation),
            "user_message": _assistant_message_to_dict(user_msg_db),
            "assistant_message": _assistant_message_to_dict(assistant_msg_db),
        })
        yield _sse_event({"type": "run", "run": run_payload(assistant_run)})
        if skill_info:
            yield _sse_event({
                "type": "skills_matched",
                "skills": skill_info,
            })

        # --- Create and execute plan ---
        orchestrator = PlanOrchestrator(db, project_id)
        plan = orchestrator.create_plan(
            graph,
            conversation_id=conversation.id,
            assistant_run_id=assistant_run.id,
            assistant_message_id=assistant_msg_db.id,
            model=model,
        )

        # Yield plan overview
        plan_steps_overview = []
        for step in sorted(plan.steps, key=lambda s: s.created_at):
            plan_steps_overview.append({
                "step_key": step.step_key,
                "tool": step.tool,
                "status": step.status,
                "label": step.detail or "",
            })

        yield _sse_event({
            "type": "plan_created",
            "plan_id": plan.id,
            "plan_name": plan.name,
            "steps": plan_steps_overview,
        })

        # Execute the plan
        tool_logs: list[dict] = []
        applied_actions: list[dict] = []
        searched_context: list[dict] = []
        _WRITE_TOOLS = registry.get_names_by_type("write")
        _SEARCH_TOOLS = (
            registry.get_names_by_type("read")
            | registry.get_names_by_type("analysis")
            | registry.get_names_by_type("web")
            | registry.get_names_by_type("memory")
            | registry.get_names_by_type("generator")
        )
        try:
            async for event in orchestrator.execute_plan(plan.id):
                if event.get("type") == "step_result":
                    step_key = event.get("step_key", "")
                    tool = event.get("tool", "")
                    status = event.get("status", "ok")

                    # Query the step row for full result data
                    step_row = db.query(AgentPlanStep).filter(
                        AgentPlanStep.plan_id == plan.id,
                        AgentPlanStep.step_key == step_key,
                    ).first()
                    full_result = None
                    if step_row and step_row.result_json:
                        try:
                            full_result = json.loads(step_row.result_json)
                        except Exception:
                            pass

                    # Enrich the event with full result data
                    enriched = {**event}
                    if full_result:
                        enriched["data"] = full_result.get("data")
                        enriched["result"] = full_result
                    yield _sse_event(enriched)

                    tool_logs.append({
                        "tool": tool,
                        "status": status,
                        "detail": event.get("detail", ""),
                        "step_key": step_key,
                    })

                    # Populate applied_actions for tools whose structured result is rendered
                    # directly under the assistant message.
                    if status == "ok" and tool in (_WRITE_TOOLS | {"preview_writing_context"}) and full_result:
                        applied_actions.append({
                            "tool": tool,
                            "status": status,
                            "detail": event.get("detail", ""),
                            "data": full_result.get("data"),
                        })

                    # Populate searched_context for search tools
                    if status == "ok" and tool in _SEARCH_TOOLS and full_result:
                        data = full_result.get("data")
                        if isinstance(data, list) and data:
                            searched_context.append({"tool": tool, "detail": event.get("detail", ""), "data": data})
                        elif isinstance(data, dict) and data:
                            searched_context.append({"tool": tool, "detail": event.get("detail", ""), "data": data})

                elif event.get("type") == "step_start":
                    yield _sse_event({
                        "type": "status",
                        "message": f"执行: {event.get('label') or event.get('tool', '')}",
                        "tool": event.get("tool", ""),
                    })
                    yield _sse_event(event)
                else:
                    yield _sse_event(event)
        except Exception as exc:
            yield _sse_event({"type": "error", "message": f"计划执行异常: {exc}"})

        # --- Finalize ---
        # Refresh plan status
        db.refresh(plan)
        final_status = plan.status

        # Build summary reply
        ok_count = sum(1 for tl in tool_logs if tl.get("status") == "ok")
        err_count = sum(1 for tl in tool_logs if tl.get("status") == "error")
        reply_parts = []
        if final_status == "completed":
            reply_parts.append(f"计划「{graph.name}」执行完成。")
        else:
            reply_parts.append(f"计划「{graph.name}」执行遇到问题。")

        if ok_count:
            reply_parts.append(f"成功 {ok_count} 个步骤。")
        if err_count:
            reply_parts.append(f"失败 {err_count} 个步骤，可以点击重试。")
            first_error = next((tl.get("detail") for tl in tool_logs if tl.get("status") == "error" and tl.get("detail")), "")
            if first_error:
                reply_parts.append(f"失败原因：{first_error}")

        final_reply = " ".join(reply_parts)

        response_payload = {
            "reply": final_reply,
            "actions": [],
            "applied_actions": applied_actions,
            "tool_logs": tool_logs,
            "searched_context": searched_context,
            "scope": scope,
            "model": model or "",
            "usage": None,
            "skills": skill_info,
            "plan": {
                "id": plan.id,
                "name": plan.name,
                "status": final_status,
            },
        }
        if assistant_run:
            response_payload["run"] = run_payload(assistant_run)

        assistant_msg_db.content = final_reply
        assistant_msg_db.payload_json = json.dumps(response_payload, ensure_ascii=False)
        assistant_msg_db.status = "completed"
        assistant_msg_db.updated_at = datetime.utcnow()
        conversation.updated_at = datetime.utcnow()
        db.commit()

        mark_assistant_run(
            db, assistant_run,
            status="completed" if final_status == "completed" else "error",
            phase="plan_completed" if final_status == "completed" else "plan_error",
            final_reply=final_reply,
        )
        db.refresh(assistant_run)
        db.refresh(assistant_msg_db)
        db.refresh(conversation)

        response_payload["run"] = run_payload(assistant_run)
        response_payload["message"] = _assistant_message_to_dict(assistant_msg_db)
        response_payload["conversation"] = _assistant_conversation_to_dict(conversation)
        yield _sse_event({"type": "complete", "data": response_payload})
        _schedule_memory_extraction(project_id, message, final_reply, model)
        yield _sse_event("[DONE]")

    return _stream()
