"""Tests for skill prompt injection into the system prompt pipeline."""

import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_skill_injection.db"

from app.prompts.packs import PromptPack
from app.services.agent.prompt_builder import build_system_prompt, build_tool_policy_section


class SkillPromptInjectionTestCase(unittest.TestCase):
    """Verify skill prompts are injected between PromptPack output and tool policy."""

    def _make_test_pack(self) -> PromptPack:
        return PromptPack(
            name="test_pack",
            version="1.0",
            pack_type="workspace",
            description="Test pack",
            input_fields=[],
            max_token_budget=1000,
            output_format="text_reply",
            output_schema=None,
            available_tools=["tool_a", "tool_b"],
            unavailable_tools=[],
            forbidden_behaviors=["禁止行为示例"],
            build_system_prompt=lambda **kw: "【基础系统提示词】\n你是一个测试助手。",
        )

    def test_no_skills(self):
        pack = self._make_test_pack()
        result = build_system_prompt(pack)
        self.assertIn("基础系统提示词", result)
        self.assertIn("工具策略", result)
        self.assertNotIn("技能", result)

    def test_with_skills(self):
        pack = self._make_test_pack()
        skill_section = "【技能：小说续写】\n你正在执行续写任务。"
        result = build_system_prompt(pack, skill_prompts=skill_section)

        # Skill prompt should appear after base prompt
        base_pos = result.find("基础系统提示词")
        skill_pos = result.find("技能：小说续写")
        policy_pos = result.find("工具策略")

        self.assertGreater(skill_pos, base_pos)
        self.assertGreater(policy_pos, skill_pos)

    def test_skill_between_pack_and_policy(self):
        pack = self._make_test_pack()
        skill_section = "【技能：测试技能】\n测试提示词内容。"
        result = build_system_prompt(pack, skill_prompts=skill_section)

        # Verify ordering: pack → skills → tool policy
        parts = ["基础系统提示词", "技能：测试技能", "工具策略"]
        positions = [result.find(p) for p in parts]
        for i in range(len(positions) - 1):
            self.assertGreater(positions[i + 1], positions[i],
                               f"'{parts[i + 1]}' should appear after '{parts[i]}'")

    def test_empty_skill_section(self):
        pack = self._make_test_pack()
        result_with_empty = build_system_prompt(pack, skill_prompts="")
        result_without = build_system_prompt(pack)
        self.assertEqual(result_with_empty, result_without)

    def test_multiple_skills(self):
        pack = self._make_test_pack()
        skill_section = "【技能：续写】\n续写提示。\n\n【技能：审校】\n审校提示。"
        result = build_system_prompt(pack, skill_prompts=skill_section)
        self.assertIn("续写提示", result)
        self.assertIn("审校提示", result)


if __name__ == "__main__":
    unittest.main()
