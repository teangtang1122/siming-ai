"""Plan orchestrator — executes a PlanGraph against the database with recovery support."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, AsyncGenerator

from sqlalchemy.orm import Session

from ...database.models import AgentPlan, AgentPlanStep
from ..workspace.executor import execute_workspace_action
from ..workspace.run_recovery import check_idempotency, generate_idempotency_key
from .plan_graph import PlanGraph, StepDef
from .step_args import resolve_step_args


_EXECUTABLE_STATUSES = {"pending", "blocked", "error"}
_SKIP_STATUSES = {"running", "ok", "skipped"}

# Tools that are known to not exist in the workspace registry.
_NOT_IMPLEMENTED_TOOLS = {"extract_facts", "resolve_targets", "apply_candidates"}


def _safe_json(data: Any, *, max_chars: int = 80_000) -> str:
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        text = json.dumps(str(data), ensure_ascii=False)
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text


def _extract_output_refs(result: dict) -> dict:
    """Extract stable reference IDs from a tool result."""
    refs: dict[str, str] = {}
    data = result.get("data") or {}
    if not isinstance(data, dict):
        return refs

    for key in ("draft_id", "content_ref", "chapter_id", "character_id",
                "worldbuilding_id", "outline_node_id", "relationship_id"):
        val = data.get(key)
        if val:
            refs[key] = str(val)

    # create_chapter returns id as chapter_id sometimes
    if "id" in data and "chapter_id" not in refs:
        tool = result.get("tool", "")
        if tool == "create_chapter":
            refs["chapter_id"] = str(data["id"])

    return refs


def _serialize_step(step: AgentPlanStep) -> dict:
    """Serialize a step to a frontend-compatible format (similar to AssistantRunStep)."""
    payload: dict[str, Any] = {
        "id": step.id,
        "step_key": step.step_key,
        "tool": step.tool,
        "status": step.status,
        "detail": step.detail,
        "error": step.error,
        "attempt_no": step.attempt_no or 1,
        "retry_of_step_id": step.retry_of_step_id,
        "resolved_step_id": step.resolved_step_id,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "completed_at": step.completed_at.isoformat() if step.completed_at else None,
        "created_at": step.created_at.isoformat() if step.created_at else None,
    }
    if step.args_json:
        try:
            payload["request"] = json.loads(step.args_json)
        except Exception:
            payload["request"] = step.args_json
    if step.result_json:
        try:
            payload["result"] = json.loads(step.result_json)
        except Exception:
            payload["result"] = step.result_json
    if step.output_refs:
        try:
            payload["output_refs"] = json.loads(step.output_refs)
        except Exception:
            payload["output_refs"] = step.output_refs
    return payload


class PlanOrchestrator:
    def __init__(self, db: Session, project_id: str):
        self.db = db
        self.project_id = project_id

    # ------------------------------------------------------------------
    # Create plan (does NOT execute)
    # ------------------------------------------------------------------

    def create_plan(
        self,
        graph: PlanGraph,
        *,
        conversation_id: str | None = None,
        assistant_run_id: str | None = None,
        assistant_message_id: str | None = None,
        model: str | None = None,
    ) -> AgentPlan:
        now = datetime.utcnow()
        plan = AgentPlan(
            project_id=self.project_id,
            conversation_id=conversation_id,
            assistant_run_id=assistant_run_id,
            assistant_message_id=assistant_message_id,
            name=graph.name,
            status="pending",
            graph_json=_safe_json({
                "name": graph.name,
                "steps": {
                    k: {
                        "tool": s.tool,
                        "args": s.args,
                        "depends_on": s.depends_on,
                        "retry_policy": s.retry_policy,
                        "idempotency_key": s.idempotency_key,
                        "label": s.label,
                    }
                    for k, s in graph.steps.items()
                },
            }),
            model=model,
            created_at=now,
            updated_at=now,
        )
        self.db.add(plan)
        self.db.flush()

        for key, step_def in graph.steps.items():
            step = AgentPlanStep(
                plan_id=plan.id,
                project_id=self.project_id,
                step_key=key,
                tool=step_def.tool,
                args_json=_safe_json(step_def.args),
                depends_on_json=json.dumps(step_def.depends_on, ensure_ascii=False),
                status="pending",
                retry_policy=step_def.retry_policy,
                idempotency_key=step_def.idempotency_key,
                attempt_no=1,
                created_at=now,
                updated_at=now,
            )
            self.db.add(step)

        self.db.commit()
        self.db.refresh(plan)
        return plan

    # ------------------------------------------------------------------
    # Execute plan
    # ------------------------------------------------------------------

    async def execute_plan(self, plan_id: str) -> AsyncGenerator[dict, None]:
        plan = self._get_plan(plan_id)
        graph = self._reconstruct_graph(plan)
        order = graph.topological_order()

        plan.status = "running"
        plan.updated_at = datetime.utcnow()
        self.db.commit()

        yield {"type": "plan_start", "plan_id": plan.id, "name": plan.name, "status": "running"}

        completed_keys: set[str] = set()
        collected_outputs: dict[str, dict] = self._collect_existing_outputs(plan)

        for step_key in order:
            step_row = self._get_step(plan.id, step_key)
            if not step_row:
                continue

            if step_row.status in _SKIP_STATUSES:
                completed_keys.add(step_key)
                yield {"type": "step_skip", "step_key": step_key, "tool": step_row.tool, "status": step_row.status}
                continue

            # Check if dependencies are met
            deps = json.loads(step_row.depends_on_json) if step_row.depends_on_json else []
            deps_met = all(d in completed_keys for d in deps)
            if not deps_met:
                if step_row.status != "blocked":
                    step_row.status = "blocked"
                    step_row.detail = f"等待依赖步骤: {', '.join(d for d in deps if d not in completed_keys)}"
                    step_row.updated_at = datetime.utcnow()
                self.db.commit()
                yield {"type": "step_blocked", "step_key": step_key, "tool": step_row.tool, "detail": step_row.detail}
                continue

            # Execute the step
            async for event in self._execute_step(plan, step_row, graph.steps[step_key], collected_outputs):
                yield event
                if event.get("type") == "step_result" and event.get("status") == "ok":
                    completed_keys.add(step_key)
                    # Re-queue blocked steps whose deps are now met
                    self._unblock_ready_steps(plan, completed_keys)

        # Determine final plan status
        all_steps = self.db.query(AgentPlanStep).filter(AgentPlanStep.plan_id == plan.id).all()
        has_error = any(s.status == "error" for s in all_steps)
        has_blocked = any(s.status == "blocked" for s in all_steps)

        if has_error:
            plan.status = "error"
            plan.error = "部分步骤执行失败"
        elif has_blocked:
            plan.status = "error"
            plan.error = "部分步骤被阻塞"
        else:
            plan.status = "completed"

        now = datetime.utcnow()
        plan.updated_at = now
        plan.completed_at = now
        self.db.commit()

        yield {"type": "plan_end", "plan_id": plan.id, "status": plan.status, "error": plan.error}

    # ------------------------------------------------------------------
    # Resume plan (retry all error/blocked steps)
    # ------------------------------------------------------------------

    async def resume_plan(self, plan_id: str) -> AsyncGenerator[dict, None]:
        plan = self._get_plan(plan_id)
        graph = self._reconstruct_graph(plan)
        order = graph.topological_order()

        plan.status = "running"
        plan.error = None
        plan.updated_at = datetime.utcnow()
        self.db.commit()

        # Reset blocked steps to pending
        blocked_steps = (
            self.db.query(AgentPlanStep)
            .filter(AgentPlanStep.plan_id == plan.id, AgentPlanStep.status == "blocked")
            .all()
        )
        for s in blocked_steps:
            s.status = "pending"
            s.updated_at = datetime.utcnow()
        self.db.commit()

        yield {"type": "plan_start", "plan_id": plan.id, "name": plan.name, "status": "running"}

        completed_keys: set[str] = set()
        collected_outputs: dict[str, dict] = self._collect_existing_outputs(plan)

        for step_key in order:
            step_row = self._get_step(plan.id, step_key)
            if not step_row:
                continue

            if step_row.status in _SKIP_STATUSES:
                completed_keys.add(step_key)
                yield {"type": "step_skip", "step_key": step_key, "tool": step_row.tool, "status": step_row.status}
                continue

            deps = json.loads(step_row.depends_on_json) if step_row.depends_on_json else []
            deps_met = all(d in completed_keys for d in deps)
            if not deps_met:
                step_row.status = "blocked"
                step_row.detail = f"等待依赖步骤: {', '.join(d for d in deps if d not in completed_keys)}"
                step_row.updated_at = datetime.utcnow()
                self.db.commit()
                yield {"type": "step_blocked", "step_key": step_key, "tool": step_row.tool, "detail": step_row.detail}
                continue

            async for event in self._execute_step(plan, step_row, graph.steps[step_key], collected_outputs):
                yield event
                if event.get("type") == "step_result" and event.get("status") == "ok":
                    completed_keys.add(step_key)
                    self._unblock_ready_steps(plan, completed_keys)

        all_steps = self.db.query(AgentPlanStep).filter(AgentPlanStep.plan_id == plan.id).all()
        has_error = any(s.status == "error" for s in all_steps)
        has_blocked = any(s.status == "blocked" for s in all_steps)

        if has_error:
            plan.status = "error"
            plan.error = "部分步骤执行失败"
        elif has_blocked:
            plan.status = "error"
            plan.error = "部分步骤被阻塞"
        else:
            plan.status = "completed"

        now = datetime.utcnow()
        plan.updated_at = now
        plan.completed_at = now
        self.db.commit()

        yield {"type": "plan_end", "plan_id": plan.id, "status": plan.status, "error": plan.error}

    # ------------------------------------------------------------------
    # Resume from a specific step
    # ------------------------------------------------------------------

    async def resume_from_step(self, plan_id: str, step_key: str) -> AsyncGenerator[dict, None]:
        plan = self._get_plan(plan_id)
        graph = self._reconstruct_graph(plan)
        order = graph.topological_order()

        if step_key not in graph.steps:
            yield {"type": "error", "detail": f"步骤 {step_key} 不存在"}
            return

        # Find downstream keys + the target itself
        downstream = graph.downstream_keys(step_key)
        scope = {step_key, *downstream}

        plan.status = "running"
        plan.error = None
        plan.updated_at = datetime.utcnow()
        self.db.commit()

        # Reset scope steps
        scope_steps = (
            self.db.query(AgentPlanStep)
            .filter(
                AgentPlanStep.plan_id == plan.id,
                AgentPlanStep.step_key.in_(scope),
            )
            .all()
        )
        for s in scope_steps:
            if s.status in {"error", "blocked", "ok"}:
                s.status = "pending"
                s.error = None
                s.updated_at = datetime.utcnow()
        self.db.commit()

        yield {"type": "plan_start", "plan_id": plan.id, "name": plan.name, "status": "running"}

        completed_keys: set[str] = set()
        collected_outputs: dict[str, dict] = self._collect_existing_outputs(plan)

        for step_key_item in order:
            if step_key_item not in scope:
                # Steps outside scope: treat their ok status as completed
                sr = self._get_step(plan.id, step_key_item)
                if sr and sr.status == "ok":
                    completed_keys.add(step_key_item)
                continue

            step_row = self._get_step(plan.id, step_key_item)
            if not step_row:
                continue

            if step_row.status in _SKIP_STATUSES:
                completed_keys.add(step_key_item)
                yield {"type": "step_skip", "step_key": step_key_item, "tool": step_row.tool, "status": step_row.status}
                continue

            deps = json.loads(step_row.depends_on_json) if step_row.depends_on_json else []
            deps_met = all(d in completed_keys for d in deps)
            if not deps_met:
                step_row.status = "blocked"
                step_row.detail = f"等待依赖步骤: {', '.join(d for d in deps if d not in completed_keys)}"
                step_row.updated_at = datetime.utcnow()
                self.db.commit()
                yield {"type": "step_blocked", "step_key": step_key_item, "tool": step_row.tool, "detail": step_row.detail}
                continue

            async for event in self._execute_step(plan, step_row, graph.steps[step_key_item], collected_outputs):
                yield event
                if event.get("type") == "step_result" and event.get("status") == "ok":
                    completed_keys.add(step_key_item)
                    self._unblock_ready_steps(plan, completed_keys)

        all_steps = self.db.query(AgentPlanStep).filter(AgentPlanStep.plan_id == plan.id).all()
        has_error = any(s.status == "error" for s in all_steps)
        has_blocked = any(s.status == "blocked" for s in all_steps)

        if has_error:
            plan.status = "error"
            plan.error = "部分步骤执行失败"
        elif has_blocked:
            plan.status = "error"
            plan.error = "部分步骤被阻塞"
        else:
            plan.status = "completed"

        now = datetime.utcnow()
        plan.updated_at = now
        plan.completed_at = now
        self.db.commit()

        yield {"type": "plan_end", "plan_id": plan.id, "status": plan.status, "error": plan.error}

    # ------------------------------------------------------------------
    # Retry a single step
    # ------------------------------------------------------------------

    async def retry_step(self, plan_id: str, step_key: str) -> dict:
        plan = self._get_plan(plan_id)
        graph = self._reconstruct_graph(plan)

        step_row = self._get_step(plan.id, step_key)
        if not step_row:
            raise ValueError(f"步骤 {step_key} 不存在")
        if step_row.status not in {"error", "blocked"}:
            raise ValueError(f"只能重试失败或阻塞的步骤，当前状态: {step_row.status}")

        step_def = graph.steps.get(step_key)
        if not step_def:
            raise ValueError(f"步骤定义 {step_key} 不存在")

        collected_outputs = self._collect_existing_outputs(plan)

        # Reset step state
        step_row.status = "pending"
        step_row.error = None
        step_row.detail = None
        step_row.updated_at = datetime.utcnow()
        self.db.commit()

        # Execute
        events = []
        async for event in self._execute_step(plan, step_row, step_def, collected_outputs):
            events.append(event)

        return _serialize_step(step_row)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_plan(self, plan_id: str) -> AgentPlan:
        plan = self.db.query(AgentPlan).filter(
            AgentPlan.id == plan_id,
            AgentPlan.project_id == self.project_id,
        ).first()
        if not plan:
            raise ValueError("计划不存在")
        return plan

    def _get_step(self, plan_id: str, step_key: str) -> AgentPlanStep | None:
        return self.db.query(AgentPlanStep).filter(
            AgentPlanStep.plan_id == plan_id,
            AgentPlanStep.step_key == step_key,
        ).first()

    def _reconstruct_graph(self, plan: AgentPlan) -> PlanGraph:
        """Reconstruct a PlanGraph from the persisted plan + steps."""
        graph_data = json.loads(plan.graph_json)
        steps: dict[str, StepDef] = {}
        for key, sdata in graph_data.get("steps", {}).items():
            steps[key] = StepDef(
                tool=sdata["tool"],
                args=sdata.get("args", {}),
                depends_on=sdata.get("depends_on", []),
                retry_policy=sdata.get("retry_policy", "none"),
                idempotency_key=sdata.get("idempotency_key"),
                label=sdata.get("label", ""),
            )
        return PlanGraph(name=graph_data.get("name", plan.name), steps=steps)

    def _collect_existing_outputs(self, plan: AgentPlan) -> dict[str, dict]:
        """Collect outputs from already-completed steps for arg resolution."""
        outputs: dict[str, dict] = {}
        completed_steps = (
            self.db.query(AgentPlanStep)
            .filter(AgentPlanStep.plan_id == plan.id, AgentPlanStep.status == "ok")
            .all()
        )
        for s in completed_steps:
            if s.result_json:
                try:
                    outputs[s.step_key] = json.loads(s.result_json)
                except Exception:
                    pass
        return outputs

    def _unblock_ready_steps(self, plan: AgentPlan, completed_keys: set[str]) -> None:
        """Re-queue blocked steps whose dependencies are now all met."""
        blocked = (
            self.db.query(AgentPlanStep)
            .filter(AgentPlanStep.plan_id == plan.id, AgentPlanStep.status == "blocked")
            .all()
        )
        for s in blocked:
            deps = json.loads(s.depends_on_json) if s.depends_on_json else []
            if all(d in completed_keys for d in deps):
                s.status = "pending"
                s.detail = None
                s.updated_at = datetime.utcnow()
        self.db.commit()

    async def _execute_step(
        self,
        plan: AgentPlan,
        step_row: AgentPlanStep,
        step_def: StepDef,
        collected_outputs: dict[str, dict],
    ) -> AsyncGenerator[dict, None]:
        """Execute a single step, yielding progress events."""
        now = datetime.utcnow()

        # State machine guard
        if step_row.status in _SKIP_STATUSES:
            yield {"type": "step_skip", "step_key": step_row.step_key, "tool": step_row.tool, "status": step_row.status}
            return

        # Check for not-implemented tools
        if step_row.tool in _NOT_IMPLEMENTED_TOOLS:
            step_row.status = "skipped"
            step_row.detail = "工具未实现，需先集成 cataloging service"
            step_row.completed_at = now
            step_row.updated_at = now
            self.db.commit()
            yield {
                "type": "step_result",
                "step_key": step_row.step_key,
                "tool": step_row.tool,
                "status": "skipped",
                "detail": step_row.detail,
                "data": {},
            }
            return

        # Mark as running
        step_row.status = "running"
        step_row.started_at = now
        step_row.updated_at = now
        self.db.commit()

        yield {
            "type": "step_start",
            "step_key": step_row.step_key,
            "tool": step_row.tool,
            "attempt_no": step_row.attempt_no,
        }

        # Resolve args
        raw_args = json.loads(step_row.args_json) if step_row.args_json else {}
        resolved_args = resolve_step_args(raw_args, collected_outputs)

        # Check idempotency
        idem_key = step_row.idempotency_key
        if not idem_key:
            idem_key = generate_idempotency_key(self.db, step_row.tool, self.project_id, resolved_args)
            if idem_key:
                step_row.idempotency_key = idem_key
                self.db.commit()

        if idem_key:
            existing = check_idempotency(self.db, self.project_id, idem_key)
            if existing:
                step_row.status = "ok"
                step_row.result_json = _safe_json(existing)
                step_row.output_refs = json.dumps(_extract_output_refs(existing), ensure_ascii=False)
                step_row.detail = "已存在，跳过重复执行（幂等）"
                step_row.completed_at = datetime.utcnow()
                step_row.updated_at = datetime.utcnow()
                self.db.commit()

                collected_outputs[step_row.step_key] = existing
                yield {
                    "type": "step_result",
                    "step_key": step_row.step_key,
                    "tool": step_row.tool,
                    "status": "ok",
                    "detail": step_row.detail,
                    "data": existing.get("data", {}),
                }
                return

        # Execute the tool
        action = {"tool": step_row.tool, "arguments": resolved_args}
        try:
            result = await execute_workspace_action(self.db, self.project_id, action)
        except Exception as exc:
            result = {"tool": step_row.tool, "status": "error", "detail": str(exc)}

        result_status = str(result.get("status") or "ok")
        result_detail = str(result.get("detail") or "")

        step_row.result_json = _safe_json(result)
        step_row.output_refs = json.dumps(_extract_output_refs(result), ensure_ascii=False)
        step_row.detail = result_detail
        step_row.completed_at = datetime.utcnow()
        step_row.updated_at = datetime.utcnow()

        if result_status == "error":
            step_row.status = "error"
            step_row.error = result_detail

            # Block downstream steps
            downstream = self._get_step_keys_after(plan.id, step_row.step_key)
            for ds_key in downstream:
                ds_step = self._get_step(plan.id, ds_key)
                if ds_step and ds_step.status == "pending":
                    ds_step.status = "blocked"
                    ds_step.detail = f"上游步骤 {step_row.step_key} 失败"
                    ds_step.updated_at = datetime.utcnow()
            self.db.commit()

            yield {
                "type": "step_result",
                "step_key": step_row.step_key,
                "tool": step_row.tool,
                "status": "error",
                "detail": result_detail,
                "data": result.get("data", {}),
            }
        else:
            step_row.status = "ok"
            self.db.commit()

            # Store output for downstream arg resolution
            collected_outputs[step_row.step_key] = result

            yield {
                "type": "step_result",
                "step_key": step_row.step_key,
                "tool": step_row.tool,
                "status": "ok",
                "detail": result_detail,
                "data": result.get("data", {}),
            }

    def _get_step_keys_after(self, plan_id: str, step_key: str) -> list[str]:
        """Get step keys that depend (transitively) on the given step."""
        all_steps = (
            self.db.query(AgentPlanStep)
            .filter(AgentPlanStep.plan_id == plan_id)
            .all()
        )
        step_map = {s.step_key: s for s in all_steps}

        # BFS to find all downstream
        downstream: list[str] = []
        queue = [step_key]
        visited = {step_key}
        while queue:
            current = queue.pop(0)
            for s in all_steps:
                deps = json.loads(s.depends_on_json) if s.depends_on_json else []
                if current in deps and s.step_key not in visited:
                    visited.add(s.step_key)
                    downstream.append(s.step_key)
                    queue.append(s.step_key)

        return downstream
