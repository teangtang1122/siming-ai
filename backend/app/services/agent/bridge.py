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


async def detect_and_stream_plan(
    db: Session,
    project_id: str,
    *,
    message: str,
    conversation_id: str | None = None,
    scope: str = "project",
    model: str | None = None,
    assistant_mode: str = "fast",
) -> AsyncGenerator[str, None] | None:
    """Detect intent and stream plan execution via SSE.

    Returns an SSE event generator if a plan intent was detected, or None
    if no plan intent was found (caller should fall back to old agentic loop).
    """
    intent = detect_intent(message)
    if intent is None:
        return None
    intent = _apply_assistant_mode_to_intent(intent, assistant_mode)

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
        _WRITE_TOOLS = {
            "create_chapter", "update_chapter", "delete_chapter",
            "create_character", "update_character", "delete_character",
            "create_outline_node", "update_outline_node", "delete_outline_node",
            "create_worldbuilding_entry", "update_worldbuilding_entry", "delete_worldbuilding_entry",
            "create_relationship", "update_relationship", "delete_relationship",
        }
        _SEARCH_TOOLS = {
            "search_characters", "search_chapters", "search_outline", "search_outline_tree",
            "search_worldbuilding", "search_relationships", "list_characters", "list_chapters",
            "list_worldbuilding", "search_context", "preview_rag_context", "preview_writing_context",
        }
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
