from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, CausalEdge, Chapter, Character, Foreshadowing, NarrativeDebt, OutlineNode, Project
from app.services.narrative_governance import (
    apply_governance_candidates,
    checkpoint_diff,
    create_narrative_checkpoint,
    governance_context,
    governance_dashboard,
    restore_narrative_checkpoint,
    upsert_causal_edge,
    upsert_foreshadowing,
    upsert_narrative_debt,
)
from app.services.chapter_service import ensure_current_snapshot


class NarrativeGovernanceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add(Project(id="p1", title="治理测试"))
        self.db.add(OutlineNode(id="o1", project_id="p1", title="第一章", node_type="chapter"))
        self.db.add(Chapter(id="c1", project_id="p1", outline_node_id="o1", title="第一章", content="正文", current_version=1))
        self.db.add(Character(id="char1", project_id="p1", name="林舟"))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_foreshadowing_deduplicates_and_transitions(self):
        first = upsert_foreshadowing(self.db, "p1", {"title": "断剑上的血纹", "importance": "high", "source_chapter_id": "c1"})
        second = upsert_foreshadowing(self.db, "p1", {"title": "断剑上的血纹", "status": "deferred", "target_chapter_number": 3})
        self.db.commit()
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.db.query(Foreshadowing).count(), 1)
        self.assertEqual(second.status, "deferred")

    def test_causal_edge_and_debt_rank_ahead_in_context(self):
        upsert_foreshadowing(self.db, "p1", {"title": "普通线索", "importance": "low"})
        edge = upsert_causal_edge(self.db, "p1", {"cause": "宗门毁约", "effect": "主角失去盟军", "strength": 0.9})
        debt = upsert_narrative_debt(self.db, "p1", {"title": "必须回应盟军背叛", "priority": "critical", "linked_causal_edge_id": edge.id})
        self.db.commit()
        context = governance_context(self.db, "p1", limit=2)
        self.assertIn(debt.title, context)
        self.assertIn(edge.effect, context)
        self.assertNotIn("普通线索", context)

    def test_candidate_batch_covers_all_structured_types(self):
        items = apply_governance_candidates(self.db, "p1", [
            {"type": "foreshadowing", "title": "门后脚步"},
            {"type": "causal_edge", "cause": "敲门", "effect": "守卫惊醒", "strength": 0.8},
            {"type": "narrative_debt", "title": "解释来客身份", "priority": "high"},
            {"type": "character_state", "character_id": "char1", "current_goal": "查明来客"},
            {"type": "quality_metric", "chapter_id": "c1", "plot_tension": 72, "character_consistency": 58, "warnings": ["角色反应偏弱"]},
        ], chapter_id="c1")
        self.db.commit()
        self.assertEqual(len(items), 5)
        dashboard = governance_dashboard(self.db, "p1")
        self.assertEqual(dashboard["counts"]["open_foreshadowings"], 1)
        self.assertEqual(len(dashboard["character_states"]), 1)
        self.assertFalse(dashboard["quality_metrics"][0]["passed"])

    def test_checkpoint_diff_and_atomic_state_restore(self):
        hook = upsert_foreshadowing(self.db, "p1", {"title": "旧伏笔", "importance": "high"})
        chapter = self.db.query(Chapter).filter(Chapter.id == "c1").one()
        ensure_current_snapshot(self.db, chapter)
        checkpoint = create_narrative_checkpoint(self.db, "p1", chapter=chapter, label="第一版")
        self.db.commit()
        hook.status = "fulfilled"
        chapter.content = "修改后的正文"
        chapter.current_version = 2
        upsert_narrative_debt(self.db, "p1", {"title": "新增债务"})
        self.db.commit()
        diff = checkpoint_diff(self.db, "p1", checkpoint.id)
        self.assertEqual(len(diff["changes"]["foreshadowings"]["changed"]), 1)
        self.assertEqual(len(diff["changes"]["narrative_debts"]["added"]), 1)
        restore_narrative_checkpoint(self.db, "p1", checkpoint.id)
        self.db.commit()
        restored = self.db.query(Foreshadowing).one()
        self.assertEqual(restored.status, "open")
        self.assertEqual(self.db.query(NarrativeDebt).count(), 0)
        restored_chapter = self.db.query(Chapter).filter(Chapter.id == "c1").one()
        self.assertEqual(restored_chapter.content, "正文")

    def test_risk_and_due_views(self):
        upsert_foreshadowing(self.db, "p1", {"title": "高风险伏笔", "importance": "critical", "target_chapter_number": 2})
        upsert_causal_edge(self.db, "p1", {"cause": "A", "effect": "B", "strength": 0.2})
        self.db.commit()
        self.assertEqual(len(governance_dashboard(self.db, "p1", chapter_id="c1", view="due")["foreshadowings"]), 1)
        risk = governance_dashboard(self.db, "p1", view="risk")
        self.assertEqual(len(risk["foreshadowings"]), 1)
        self.assertEqual(len(risk["causal_edges"]), 0)


if __name__ == "__main__":
    unittest.main()
