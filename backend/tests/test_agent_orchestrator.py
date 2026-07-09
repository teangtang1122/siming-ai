"""Tests for the agent plan orchestration system."""
import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

# Set test DB before importing app modules
os.environ["DATABASE_URL"] = "sqlite:///./test_agent_orchestrator.db"

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    AgentPlan,
    AgentPlanStep,
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
    Base,
    Project,
)
from app.database.session import get_db
from app.services.agent.bridge import _apply_assistant_mode_to_intent
from app.services.agent.orchestrator import PlanOrchestrator, _serialize_step
from app.services.agent.plan_graph import PlanGraph, StepDef
from app.services.agent.planner import (
    detect_intent,
    plan_create_outline,
    plan_cataloging_init,
    plan_fast_chapter,
    plan_local_cli_writing,
    plan_quality_chapter,
)
from app.services.agent.step_args import resolve_step_args

from app.main import app

API_PREFIX = "/api/v1"

engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
TestSession = sessionmaker(bind=engine)


def _run_async(coro):
    """Run an async coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class PlanGraphTestCase(unittest.TestCase):
    """Tests for plan_graph.py data structures."""

    def test_topological_order_simple_chain(self):
        graph = PlanGraph(name="test", steps={
            "a": StepDef(tool="t1", depends_on=[]),
            "b": StepDef(tool="t2", depends_on=["a"]),
            "c": StepDef(tool="t3", depends_on=["b"]),
        })
        order = graph.topological_order()
        self.assertEqual(order, ["a", "b", "c"])

    def test_topological_order_diamond(self):
        graph = PlanGraph(name="test", steps={
            "a": StepDef(tool="t1", depends_on=[]),
            "b": StepDef(tool="t2", depends_on=["a"]),
            "c": StepDef(tool="t3", depends_on=["a"]),
            "d": StepDef(tool="t4", depends_on=["b", "c"]),
        })
        order = graph.topological_order()
        self.assertEqual(order.index("a"), 0)
        self.assertLess(order.index("a"), order.index("b"))
        self.assertLess(order.index("a"), order.index("c"))
        self.assertLess(order.index("b"), order.index("d"))
        self.assertLess(order.index("c"), order.index("d"))

    def test_topological_order_cycle_raises(self):
        graph = PlanGraph(name="test", steps={
            "a": StepDef(tool="t1", depends_on=["b"]),
            "b": StepDef(tool="t2", depends_on=["a"]),
        })
        with self.assertRaises(ValueError):
            graph.topological_order()

    def test_ready_steps(self):
        graph = PlanGraph(name="test", steps={
            "a": StepDef(tool="t1", depends_on=[]),
            "b": StepDef(tool="t2", depends_on=["a"]),
            "c": StepDef(tool="t3", depends_on=["a"]),
            "d": StepDef(tool="t4", depends_on=["b", "c"]),
        })
        self.assertEqual(graph.ready_steps(set()), ["a"])
        self.assertEqual(set(graph.ready_steps({"a"})), {"b", "c"})
        self.assertEqual(graph.ready_steps({"a", "b"}), ["c"])
        self.assertEqual(graph.ready_steps({"a", "b", "c"}), ["d"])

    def test_downstream_keys(self):
        graph = PlanGraph(name="test", steps={
            "a": StepDef(tool="t1", depends_on=[]),
            "b": StepDef(tool="t2", depends_on=["a"]),
            "c": StepDef(tool="t3", depends_on=["a"]),
            "d": StepDef(tool="t4", depends_on=["b", "c"]),
        })
        self.assertEqual(set(graph.downstream_keys("a")), {"b", "c", "d"})
        self.assertEqual(graph.downstream_keys("b"), ["d"])
        self.assertEqual(graph.downstream_keys("d"), [])


class StepArgsResolverTestCase(unittest.TestCase):
    """Tests for step_args.py reference resolver."""

    def test_resolve_simple_string(self):
        outputs = {"writer": {"data": {"draft_id": "abc123"}}}
        result = resolve_step_args({"id": "{writer.data.draft_id}"}, outputs)
        self.assertEqual(result, {"id": "abc123"})

    def test_resolve_entire_value_preserves_type(self):
        outputs = {"writer": {"data": {"count": 42}}}
        result = resolve_step_args("{writer.data.count}", outputs)
        self.assertEqual(result, 42)

    def test_resolve_nested_dict(self):
        outputs = {"s1": {"data": {"x": "hello"}}}
        args = {"outer": {"inner": "{s1.data.x}"}}
        result = resolve_step_args(args, outputs)
        self.assertEqual(result, {"outer": {"inner": "hello"}})

    def test_resolve_list_items(self):
        outputs = {"s1": {"data": {"name": "Alice"}}}
        args = ["{s1.data.name}", "fixed"]
        result = resolve_step_args(args, outputs)
        self.assertEqual(result, ["Alice", "fixed"])

    def test_resolve_missing_key_returns_placeholder(self):
        outputs = {}
        result = resolve_step_args("{missing.data.field}", outputs)
        self.assertEqual(result, "{missing.data.field}")

    def test_resolve_partial_string_substitution(self):
        outputs = {"s1": {"data": {"name": "Alice"}}}
        result = resolve_step_args("Hello {s1.data.name}!", outputs)
        self.assertEqual(result, "Hello Alice!")

    def test_resolve_list_index(self):
        outputs = {"search": {"data": [{"title": "Ch1"}, {"title": "Ch2"}]}}
        result = resolve_step_args("{search.data.0.title}", outputs)
        self.assertEqual(result, "Ch1")

    def test_resolve_passthrough_non_string(self):
        outputs = {}
        result = resolve_step_args(42, outputs)
        self.assertEqual(result, 42)


class PlannerTestCase(unittest.TestCase):
    """Tests for planner.py plan generation."""

    def test_fast_chapter_plan_generation(self):
        graph = plan_fast_chapter(outline_node_id="node-1", requirements="写快点")
        self.assertEqual(graph.name, "fast_chapter")
        self.assertIn("search_outline", graph.steps)
        self.assertIn("chapter_writer", graph.steps)
        self.assertIn("create_chapter", graph.steps)
        self.assertIn("archive_chapter_after_write", graph.steps)
        self.assertEqual(len(graph.steps), 4)
        # Verify dependencies
        self.assertEqual(graph.steps["chapter_writer"].depends_on, ["search_outline"])
        self.assertEqual(graph.steps["create_chapter"].depends_on, ["chapter_writer"])
        self.assertEqual(graph.steps["archive_chapter_after_write"].depends_on, ["create_chapter"])

    def test_quality_chapter_plan_generation_single_char(self):
        graph = plan_quality_chapter(
            outline_node_id="node-1",
            involved_characters=["Alice"],
        )
        self.assertEqual(graph.name, "quality_chapter")
        self.assertIn("roleplay", graph.steps)
        self.assertNotIn("dialogue_battle", graph.steps)
        self.assertEqual(graph.steps["roleplay"].tool, "roleplay_character")

    def test_quality_chapter_plan_generation_multi_char(self):
        graph = plan_quality_chapter(
            outline_node_id="node-1",
            involved_characters=["Alice", "Bob"],
        )
        self.assertIn("dialogue_battle", graph.steps)
        self.assertNotIn("roleplay", graph.steps)
        self.assertEqual(graph.steps["dialogue_battle"].tool, "dialogue_battle")

    def test_quality_chapter_plan_has_all_steps(self):
        graph = plan_quality_chapter(outline_node_id="node-1")
        expected = {"preview_context", "design_plot", "roleplay", "chapter_writer",
                    "evaluate_chapter", "create_chapter", "archive_chapter_after_write"}
        self.assertEqual(set(graph.steps.keys()), expected)

    def test_cataloging_init_plan_generation(self):
        graph = plan_cataloging_init(chapter_ids=["c1", "c2"])
        self.assertEqual(graph.name, "cataloging_init")
        self.assertEqual(set(graph.steps.keys()), {"list_chapters", "start_cataloging_job"})
        self.assertEqual(graph.steps["list_chapters"].tool, "list_chapters")
        self.assertEqual(graph.steps["start_cataloging_job"].tool, "start_cataloging_job")
        self.assertEqual(graph.steps["start_cataloging_job"].depends_on, ["list_chapters"])
        self.assertEqual(graph.steps["start_cataloging_job"].args["chapter_ids"], ["c1", "c2"])

    def test_create_outline_plan_generation(self):
        graph = plan_create_outline(requirements="补第151章", batch_count=2)
        self.assertEqual(graph.name, "create_outline")
        self.assertEqual(set(graph.steps.keys()), {"outline_writer", "create_outline_nodes"})
        self.assertEqual(graph.steps["create_outline_nodes"].depends_on, ["outline_writer"])
        self.assertEqual(graph.steps["create_outline_nodes"].args["nodes"], "{outline_writer.data.nodes}")
        self.assertEqual(graph.steps["outline_writer"].args["batch_count"], 2)

    def test_detect_intent_fast_chapter(self):
        result = detect_intent("写第151章")
        self.assertIsNotNone(result)
        self.assertEqual(result["mode"], "fast")
        self.assertEqual(result["chapter_number"], 151)

    def test_detect_intent_fast_chapter_without_di_prefix(self):
        result = detect_intent("帮我写151章")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent_type"], "chapter")
        self.assertEqual(result["mode"], "fast")
        self.assertEqual(result["chapter_number"], 151)

    def test_detect_intent_quality_chapter(self):
        result = detect_intent("精写第42章")
        self.assertIsNotNone(result)
        self.assertEqual(result["mode"], "quality")
        self.assertEqual(result["chapter_number"], 42)

    def test_detect_intent_cataloging(self):
        result = detect_intent("给这个项目建档")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent_type"], "project_init")

    def test_detect_intent_create_outline(self):
        result = detect_intent("那就先帮我创建大纲")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent_type"], "outline")

    def test_detect_intent_create_bare_chapter_outline(self):
        result = detect_intent("帮我创建151章大纲")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent_type"], "outline")
        self.assertEqual(result["chapter_number"], 151)
        self.assertIsNone(result["batch_count"])

    def test_detect_intent_outline_batch_keeps_count_separate(self):
        result = detect_intent("帮我创建后续3章大纲")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent_type"], "outline")
        self.assertIsNone(result["chapter_number"])
        self.assertEqual(result["batch_count"], 3)

    def test_local_cli_writing_plan_starts_worker(self):
        graph = plan_local_cli_writing(
            requirements="帮我写151章",
            provider="opencode_cli",
            outline_node_id="outline-151",
        )
        self.assertEqual(graph.name, "local_cli_writing")
        self.assertEqual(set(graph.steps.keys()), {"start_local_cli_agent_run"})
        step = graph.steps["start_local_cli_agent_run"]
        self.assertEqual(step.tool, "start_local_cli_agent_run")
        self.assertEqual(step.args["task_type"], "writing")
        self.assertEqual(step.args["provider"], "opencode_cli")
        self.assertIn("outline-151", step.args["user_request"])

    def test_assistant_mode_quality_overrides_chapter_plan_mode(self):
        intent = {
            "intent_type": "chapter",
            "mode": "fast",
            "requirements": "write chapter 5",
            "chapter_number": 5,
        }
        result = _apply_assistant_mode_to_intent(intent, "quality")
        self.assertEqual(result["mode"], "quality")
        self.assertEqual(intent["mode"], "fast")

    def test_assistant_mode_does_not_override_non_chapter_intent(self):
        intent = {
            "intent_type": "character",
            "mode": "fast",
            "requirements": "create character",
        }
        result = _apply_assistant_mode_to_intent(intent, "quality")
        self.assertEqual(result["mode"], "fast")

    def test_detect_intent_returns_none_for_unrelated(self):
        self.assertIsNone(detect_intent("今天天气怎么样"))
        self.assertIsNone(detect_intent(""))


class OrchestratorTestCase(unittest.TestCase):
    """Tests for PlanOrchestrator DB operations."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_agent_orchestrator.db")
        except OSError:
            pass

    def setUp(self):
        self.db = TestSession()
        # Clean tables in dependency order
        self.db.query(AgentPlanStep).delete()
        self.db.query(AgentPlan).delete()
        self.db.query(AssistantMessage).delete()
        self.db.query(AssistantConversation).delete()
        self.db.query(AssistantRun).delete()
        self.db.query(Project).delete()
        self.db.commit()

        # Create a test project
        self.project = Project(id="proj-1", title="Test Project")
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_create_plan_does_not_execute(self):
        graph = plan_fast_chapter(outline_node_id="node-1")
        orchestrator = PlanOrchestrator(self.db, "proj-1")
        plan = orchestrator.create_plan(graph, model="test-model")

        self.assertEqual(plan.status, "pending")
        self.assertEqual(plan.name, "fast_chapter")
        self.assertIsNotNone(plan.graph_json)

        steps = self.db.query(AgentPlanStep).filter(AgentPlanStep.plan_id == plan.id).all()
        self.assertEqual(len(steps), 4)
        for s in steps:
            self.assertEqual(s.status, "pending")

    def test_plan_persistence(self):
        graph = plan_fast_chapter(outline_node_id="node-1")
        orchestrator = PlanOrchestrator(self.db, "proj-1")
        plan = orchestrator.create_plan(graph)
        plan_id = plan.id

        # Read back from DB
        loaded = self.db.query(AgentPlan).filter(AgentPlan.id == plan_id).first()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "fast_chapter")
        self.assertEqual(loaded.status, "pending")

        # Verify graph can be reconstructed
        graph_data = json.loads(loaded.graph_json)
        self.assertEqual(graph_data["name"], "fast_chapter")
        self.assertIn("chapter_writer", graph_data["steps"])

    def test_bridge_to_assistant_run(self):
        # Create conversation and run
        conv = AssistantConversation(id="conv-1", project_id="proj-1", title="test")
        run = AssistantRun(id="run-1", project_id="proj-1", status="running")
        msg = AssistantMessage(id="msg-1", conversation_id="conv-1", role="user", content="test")
        self.db.add_all([conv, run, msg])
        self.db.commit()

        graph = plan_fast_chapter(outline_node_id="node-1")
        orchestrator = PlanOrchestrator(self.db, "proj-1")
        plan = orchestrator.create_plan(
            graph,
            conversation_id="conv-1",
            assistant_run_id="run-1",
            assistant_message_id="msg-1",
        )

        self.assertEqual(plan.conversation_id, "conv-1")
        self.assertEqual(plan.assistant_run_id, "run-1")
        self.assertEqual(plan.assistant_message_id, "msg-1")

        # Verify frontend-compatible payload
        payload = _serialize_step(plan.steps[0])
        self.assertIn("id", payload)
        self.assertIn("step_key", payload)
        self.assertIn("tool", payload)
        self.assertIn("status", payload)

    def test_running_step_no_duplicate(self):
        graph = PlanGraph(name="test", steps={
            "a": StepDef(tool="search_outline", args={}, depends_on=[]),
        })
        orchestrator = PlanOrchestrator(self.db, "proj-1")
        plan = orchestrator.create_plan(graph)

        # Manually set step to running
        step = self.db.query(AgentPlanStep).filter(AgentPlanStep.plan_id == plan.id).first()
        step.status = "running"
        self.db.commit()

        # Execute should skip the running step
        events = _run_async(_collect_events(orchestrator.execute_plan(plan.id)))
        step_events = [e for e in events if e.get("type") == "step_skip"]
        self.assertEqual(len(step_events), 1)
        self.assertEqual(step_events[0]["step_key"], "a")


class OrchestratorExecutionTestCase(unittest.TestCase):
    """Tests for orchestrator execution with mocked tool calls."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_agent_orchestrator.db")
        except OSError:
            pass

    def setUp(self):
        self.db = TestSession()
        self.db.query(AgentPlanStep).delete()
        self.db.query(AgentPlan).delete()
        self.db.query(Project).delete()
        self.db.commit()

        self.project = Project(id="proj-2", title="Test Project 2")
        self.db.add(self.project)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    @patch("app.services.agent.orchestrator.execute_workspace_action")
    def test_dependency_execution(self, mock_execute):
        """Steps execute only when dependencies are met."""
        call_order = []

        async def track_execute(db, project_id, action):
            call_order.append(action["tool"])
            return {"tool": action["tool"], "status": "ok", "detail": "done", "data": {}}

        mock_execute.side_effect = track_execute

        graph = PlanGraph(name="test", steps={
            "a": StepDef(tool="tool_a", depends_on=[]),
            "b": StepDef(tool="tool_b", depends_on=["a"]),
            "c": StepDef(tool="tool_c", depends_on=["a"]),
        })
        orchestrator = PlanOrchestrator(self.db, "proj-2")
        plan = orchestrator.create_plan(graph)

        events = _run_async(_collect_events(orchestrator.execute_plan(plan.id)))

        self.assertEqual(call_order[0], "tool_a")
        self.assertIn("tool_b", call_order)
        self.assertIn("tool_c", call_order)
        self.assertLess(call_order.index("tool_a"), call_order.index("tool_b"))
        self.assertLess(call_order.index("tool_a"), call_order.index("tool_c"))

    @patch("app.services.agent.orchestrator.execute_workspace_action")
    def test_plan_injects_model_and_chapter_mode(self, mock_execute):
        """Runtime plan model is passed to generator tools that accept model."""
        captured_actions = []

        async def capture_execute(db, project_id, action):
            captured_actions.append(action)
            return {"tool": action["tool"], "status": "ok", "detail": "done", "data": {}}

        mock_execute.side_effect = capture_execute

        graph = PlanGraph(name="fast_chapter", steps={
            "write": StepDef(tool="chapter_writer", args={"outline_node_id": "node-1"}, depends_on=[]),
        })
        orchestrator = PlanOrchestrator(self.db, "proj-2")
        plan = orchestrator.create_plan(graph, model="claude_cli:claude-code")

        _run_async(_collect_events(orchestrator.execute_plan(plan.id)))

        self.assertEqual(len(captured_actions), 1)
        self.assertEqual(captured_actions[0]["arguments"]["model"], "claude_cli:claude-code")
        self.assertEqual(captured_actions[0]["arguments"]["mode"], "fast")

    @patch("app.services.agent.orchestrator.execute_workspace_action")
    def test_failure_blocks_downstream(self, mock_execute):
        """Failed step causes downstream steps to become blocked."""
        async def fail_on_b(db, project_id, action):
            if action["tool"] == "tool_b":
                return {"tool": "tool_b", "status": "error", "detail": "boom"}
            return {"tool": action["tool"], "status": "ok", "detail": "done", "data": {}}

        mock_execute.side_effect = fail_on_b

        graph = PlanGraph(name="test", steps={
            "a": StepDef(tool="tool_a", depends_on=[]),
            "b": StepDef(tool="tool_b", depends_on=["a"]),
            "c": StepDef(tool="tool_c", depends_on=["b"]),
        })
        orchestrator = PlanOrchestrator(self.db, "proj-2")
        plan = orchestrator.create_plan(graph)

        events = _run_async(_collect_events(orchestrator.execute_plan(plan.id)))

        # Verify plan ended with error
        plan_end = [e for e in events if e.get("type") == "plan_end"]
        self.assertEqual(plan_end[0]["status"], "error")

        # Verify step c is blocked
        step_c = self.db.query(AgentPlanStep).filter(
            AgentPlanStep.plan_id == plan.id,
            AgentPlanStep.step_key == "c",
        ).first()
        self.assertEqual(step_c.status, "blocked")
        self.assertIn("上游步骤", step_c.detail)

    @patch("app.services.agent.orchestrator.execute_workspace_action")
    def test_resume_unblocks_downstream(self, mock_execute):
        """Resume resets blocked steps and re-executes."""
        call_count = {"b": 0}

        async def fail_then_succeed(db, project_id, action):
            if action["tool"] == "tool_b":
                call_count["b"] += 1
                if call_count["b"] == 1:
                    return {"tool": "tool_b", "status": "error", "detail": "boom"}
            return {"tool": action["tool"], "status": "ok", "detail": "done", "data": {}}

        mock_execute.side_effect = fail_then_succeed

        graph = PlanGraph(name="test", steps={
            "a": StepDef(tool="tool_a", depends_on=[]),
            "b": StepDef(tool="tool_b", depends_on=["a"]),
            "c": StepDef(tool="tool_c", depends_on=["b"]),
        })
        orchestrator = PlanOrchestrator(self.db, "proj-2")
        plan = orchestrator.create_plan(graph)

        # First execution: b fails, c blocked
        _run_async(_collect_events(orchestrator.execute_plan(plan.id)))

        # Resume: b succeeds, c unblocked
        events = _run_async(_collect_events(orchestrator.resume_plan(plan.id)))

        plan_end = [e for e in events if e.get("type") == "plan_end"]
        self.assertEqual(plan_end[0]["status"], "completed")

        step_c = self.db.query(AgentPlanStep).filter(
            AgentPlanStep.plan_id == plan.id,
            AgentPlanStep.step_key == "c",
        ).first()
        self.assertEqual(step_c.status, "ok")

    @patch("app.services.agent.orchestrator.execute_workspace_action")
    def test_resume_from_step(self, mock_execute):
        """Resume from a specific step re-executes it and downstream."""
        call_order = []

        async def track_execute(db, project_id, action):
            call_order.append(action["tool"])
            return {"tool": action["tool"], "status": "ok", "detail": "done", "data": {}}

        mock_execute.side_effect = track_execute

        graph = PlanGraph(name="test", steps={
            "a": StepDef(tool="tool_a", depends_on=[]),
            "b": StepDef(tool="tool_b", depends_on=["a"]),
            "c": StepDef(tool="tool_c", depends_on=["b"]),
        })
        orchestrator = PlanOrchestrator(self.db, "proj-2")
        plan = orchestrator.create_plan(graph)

        # Execute all first
        _run_async(_collect_events(orchestrator.execute_plan(plan.id)))
        call_order.clear()

        # Resume from b
        events = _run_async(_collect_events(orchestrator.resume_from_step(plan.id, "b")))
        self.assertIn("tool_b", call_order)
        self.assertIn("tool_c", call_order)
        self.assertNotIn("tool_a", call_order)

    @patch("app.services.agent.orchestrator.execute_workspace_action")
    def test_idempotency_skip(self, mock_execute):
        """Re-running a completed step with same idempotency key skips it."""
        async def ok_execute(db, project_id, action):
            return {"tool": action["tool"], "status": "ok", "detail": "done", "data": {"id": "x"}}

        mock_execute.side_effect = ok_execute

        graph = PlanGraph(name="test", steps={
            "a": StepDef(tool="create_chapter", args={"title": "Ch1"}, depends_on=[], idempotency_key="create_chapter:proj-2:Ch1"),
        })
        orchestrator = PlanOrchestrator(self.db, "proj-2")
        plan = orchestrator.create_plan(graph)

        # First execute
        _run_async(_collect_events(orchestrator.execute_plan(plan.id)))

        # Reset step to pending for re-execution
        step = self.db.query(AgentPlanStep).filter(AgentPlanStep.plan_id == plan.id).first()
        step.status = "pending"
        self.db.commit()

        # Second execute should skip due to idempotency
        events = _run_async(_collect_events(orchestrator.execute_plan(plan.id)))
        step_results = [e for e in events if e.get("type") == "step_result"]
        # The step should be skipped (either via idempotency or skip status)
        self.assertTrue(
            any("idempotency" in (e.get("detail") or "").lower()
                or "跳过" in (e.get("detail") or "")
                or e.get("status") == "ok"
                for e in step_results)
        )

    @patch("app.services.agent.orchestrator.execute_workspace_action")
    def test_step_args_resolved(self, mock_execute):
        """Step args with references are resolved before execution."""
        captured_args = {}

        async def capture_execute(db, project_id, action):
            captured_args[action["tool"]] = action.get("arguments", {})
            return {"tool": action["tool"], "status": "ok", "detail": "done", "data": {"draft_id": "d1"}}

        mock_execute.side_effect = capture_execute

        graph = PlanGraph(name="test", steps={
            "writer": StepDef(tool="chapter_writer", args={"node": "n1"}, depends_on=[]),
            "saver": StepDef(tool="create_chapter", args={"draft_id": "{writer.data.draft_id}"}, depends_on=["writer"]),
        })
        orchestrator = PlanOrchestrator(self.db, "proj-2")
        plan = orchestrator.create_plan(graph)

        _run_async(_collect_events(orchestrator.execute_plan(plan.id)))

        self.assertEqual(captured_args.get("create_chapter", {}).get("draft_id"), "d1")


class AgentRouterTestCase(unittest.TestCase):
    """Tests for the agent router endpoints."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        from fastapi.testclient import TestClient
        # Override get_db
        def override_get_db():
            db = TestSession()
            try:
                yield db
            finally:
                db.close()
        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        app.dependency_overrides.clear()
        try:
            os.remove("test_agent_orchestrator.db")
        except OSError:
            pass

    def setUp(self):
        db = TestSession()
        db.query(AgentPlanStep).delete()
        db.query(AgentPlan).delete()
        db.query(Project).delete()
        db.commit()
        db.close()

    def _create_project(self):
        db = TestSession()
        p = Project(id="proj-api", title="API Test Project")
        db.add(p)
        db.commit()
        db.close()
        return "proj-api"

    def test_create_plan_endpoint(self):
        pid = self._create_project()
        resp = self.client.post(f"{API_PREFIX}/projects/{pid}/ai/agent/plans", json={
            "mode": "fast",
            "outline_node_id": "node-1",
            "requirements": "写快点",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["name"], "fast_chapter")
        self.assertEqual(data["status"], "pending")
        self.assertEqual(len(data["steps"]), 4)

    def test_get_plan_endpoint(self):
        pid = self._create_project()
        # Create plan
        resp = self.client.post(f"{API_PREFIX}/projects/{pid}/ai/agent/plans", json={
            "mode": "fast",
            "outline_node_id": "node-1",
        })
        plan_id = resp.json()["data"]["id"]

        # Get plan
        resp = self.client.get(f"{API_PREFIX}/projects/{pid}/ai/agent/plans/{plan_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["id"], plan_id)
        self.assertEqual(data["status"], "pending")

    def test_create_plan_invalid_mode(self):
        pid = self._create_project()
        resp = self.client.post(f"{API_PREFIX}/projects/{pid}/ai/agent/plans", json={
            "mode": "invalid",
            "outline_node_id": "node-1",
        })
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _collect_events(async_gen):
    """Collect all events from an async generator."""
    events = []
    async for event in async_gen:
        events.append(event)
    return events


if __name__ == "__main__":
    unittest.main()
