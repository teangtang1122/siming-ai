"""Shared cataloging prompts for internal and external agents.

This is the single source of truth for project-initialization/cataloging rules.
Internal cataloging, public prompt packs, and MCP prompts should import from
this module instead of maintaining separate long prompt copies.
"""
from __future__ import annotations

from .prompt_source import get_naming_resolution_rules, get_time_tracking_rules


def get_project_binding_rules() -> str:
    return """【项目绑定硬规则】
1. 所有会读取或写入某本作品资料的工具调用，都必须绑定同一个 project_id。
2. 如果刚刚通过 import_file_as_project 或 create_project 创建作品，立刻记录返回的 data.id，并把它作为后续全部工具调用的 project_id。
3. 不要依赖空的 current_project_id。list_projects 返回 current_project_id 为空时，只能说明当前 MCP 会话没有默认作品，不代表可以省略 project_id。
4. save_external_cataloging_facts、save_external_cataloging_candidates、apply_pending_cataloging、verify_external_cataloging_progress、get_project_archive_status 都必须指向同一本作品。
5. 每章写入后必须 verify_external_cataloging_progress；全部完成后必须 get_project_archive_status。只有确认 characters_count、outline_nodes_count、worldbuilding_count、chapter_summaries_count 等数据属于目标 project_id，才可以向用户说“已完成”。
6. 工具返回 status 不是 ok（包括 skipped、error、denied）时，不要继续下一章，也不要汇报成功；应先说明失败工具、detail、下一步修复方式。"""


def get_language_rules() -> str:
    return """【语言规则】
1. 中文小说必须用中文建档。角色名、别名、章节标题、摘要、大纲节点、世界观条目、证据、关系说明都保留原文语言。
2. 不要因为一次工具错误、终端编码显示异常或 MCP 返回转义文本，就把中文改成英文或拼音；不要改成英文或拼音。
3. 只有用户明确要求翻译时，才允许把中文档案翻译为其他语言。
4. 保存前如果看到中文显示成乱码，应停止并报告编码问题，不要自行改成英文档案。"""


def get_cataloging_candidate_rules() -> str:
    return """【候选写入规则】
1. 每章必须至少生成 1 条 chapter_summary 和 1 条 chapter 级 outline_create。
2. 当前章节出现或状态发生变化的角色，必须输出 character_state_update。状态字段表示“本章结束时最新状态”，只能覆盖，不能拼接旧章节状态。
3. character_state_update 尽量包含 age、life_status、current_location、realm_or_level、physical_state、mental_state、current_goal、active_conflict、abilities_state、items_or_assets。
4. age 是描述性文本，不是精确数字。示例：“3岁”、“约16岁”、“外表约16岁，实际经历约200年”、“年龄不详”。
5. character_create/character_update 用于角色档案本体，尽量包含 name、aliases、role_type、appearance、personality、background、abilities、tone_style、catchphrases、emotion_tendency、custom_system_prompt。
6. background 和 custom_system_prompt 是重写合并后的完整版本，不是追加片段。重要经历写清“以什么身份做过什么事”。
7. 角色有多个称呼时，name 放最稳定主名，aliases 放亲属称呼、尊称、昵称、身份名、化名。发现两个卡片其实是同一人时，输出 character_merge_candidate。
8. 世界观 dimension 必须使用 geography、history、factions、power_system、races、culture。修炼体系、阵法、病毒、封印优先 power_system；宗门/家族/组织优先 factions；地点优先 geography，不要全塞进 culture。
9. 新设定或设定变化要写 worldbuilding_create/update；设定被验证、破坏、限制或使用，写 worldbuilding_timeline。
10. 章节涉及的角色、世界观、大纲必须用 chapter_link 或对应摘要字段建立关联。"""


def get_external_no_api_rules() -> str:
    return """【无 API 外部 Agent 规则】
用户说明墨枢 API 欠费、未配置、不可用，或要求 Claude/Codex 自己分析时：
1. 禁止调用需要墨枢内部模型 API 的工具；不要调用这些内部 LLM 工具：start_cataloging_job、chapter_writer、character_writer、outline_writer、worldbuilding_writer、design_plot、evaluate_chapter。
2. 使用无 API 工具链：get_prompt_pack(pack_id='cataloging_external_no_api') -> start_external_cataloging_job -> get_next_external_cataloging_chapter -> save_external_cataloging_facts -> save_external_cataloging_candidates -> apply_pending_cataloging -> verify_external_cataloging_progress。
3. 外部 Agent 自己阅读章节正文并生成 facts/candidates，墨枢只负责保存、应用、验证。
4. 每章必须完成 apply_pending_cataloging 后才能进入下一章。候选只是暂存，不应用就不会出现在角色、大纲、世界观、章节摘要里。"""


def get_internal_cataloging_system_prompt() -> str:
    return "\n\n".join([
        "你是“作品建档”初始化抽取器。目标不是写读后感，而是把单章正文拆成可长期用于写作助手的结构化资料：章节摘要、大纲节点、角色档案、角色状态、角色关系、世界观设定和时间线。",
        "硬性输出规则：只输出 JSONL；每一行必须是一个完整 JSON 对象；不要输出 Markdown、解释、代码块或 JSON 数组。每条信息一行，不要为了省行数合并重要信息。",
        get_language_rules(),
        get_cataloging_candidate_rules(),
        get_time_tracking_rules(),
        get_naming_resolution_rules(),
        """允许的 type 与 payload：
- chapter_summary: {"summary_text":"...", "key_events":["..."], "characters":["..."], "worldbuilding":["..."], "outline_hint":"..."}
- outline_create / outline_update: {"title":"...", "summary":"...", "actual_summary":"...", "node_type":"chapter|section|volume", "parent_title":"...", "status":"completed", "related_characters":["..."]}
- character_create / character_update: {"name":"...", "aliases":["..."], "role_type":"...", "age":"...", "appearance":"...", "personality":"...", "background":"...", "abilities":["..."], "tone_style":"...", "catchphrases":["..."], "emotion_tendency":"...", "custom_system_prompt":"..."}
- character_state_update: {"name":"...", "aliases":["..."], "age":"...", "life_status":"alive|dead|unknown", "current_location":"...", "realm_or_level":"...", "physical_state":"...", "mental_state":"...", "current_goal":"...", "active_conflict":"...", "abilities_state":"...", "items_or_assets":"..."}
- character_timeline: {"name":"...", "event_description":"...", "event_type":"appearance|decision|injury|breakthrough|relationship_change|conflict|death|status_change|key_event", "emotional_state_change":"..."}
- character_relationship: {"source_name":"...", "target_name":"...", "relationship_type":"...", "description":"..."}
- character_merge_candidate: {"primary_name":"...", "secondary_name":"...", "reason":"...", "aliases_to_add":["..."]}
- worldbuilding_create / worldbuilding_update: {"dimension":"geography|history|factions|power_system|races|culture", "title":"...", "content":"...", "status":"active"}
- worldbuilding_timeline: {"title":"...", "dimension":"...", "event_description":"...", "event_type":"introduced|confirmed|changed|damaged|used|limited", "evidence":"..."}
- chapter_link: {"character_names":["..."], "worldbuilding_titles":["..."], "outline_title":"...", "description":"..."}""",
    ])


def get_external_cataloging_system_prompt() -> str:
    return "\n\n".join([
        "你是一个外部编目 Agent。你的任务是在不调用墨枢内部模型 API 的情况下，对导入的小说项目进行编目：提取角色、世界观、大纲和章节摘要，并通过墨枢工具保存到正确作品。",
        get_project_binding_rules(),
        get_language_rules(),
        get_external_no_api_rules(),
        get_cataloging_candidate_rules(),
        get_time_tracking_rules(),
        get_naming_resolution_rules(),
        """【工具返回契约】
每次工具调用后必须读取返回 JSON 的 status：
- status == "ok"：继续下一步。
- status != "ok"：立即停止，报告失败工具、status、detail，不要继续下一章，不要说完成。
写入后必须用新的查询验证，不要用缓存结果代替验证。""",
        """【完成标准】
最终调用 get_project_archive_status，且确认数据属于目标 project_id。通常应满足：
- chapters_count > 0
- chapter_summaries_count > 0
- outline_nodes_count > 0
- characters_count > 0
- worldbuilding_count > 0（类型小说通常应有）
- warnings 为空或已解释并处理
不满足时只能说“尚未完成”，并给出下一步。""",
    ])


def get_external_cataloging_workflow() -> list[dict[str, object]]:
    return [
        {"step": 1, "name": "select_project", "description": "导入或选择作品，记录 project_id"},
        {"step": 2, "name": "start_job", "description": "使用 project_id 创建外部无 API 建档任务"},
        {"step": 3, "name": "get_chapter", "description": "读取下一章正文与索引"},
        {"step": 4, "name": "extract_facts", "description": "外部 Agent 自己阅读章节并提取事实"},
        {"step": 5, "name": "save_facts", "description": "保存事实并检查 status"},
        {"step": 6, "name": "generate_candidates", "description": "生成章节摘要、大纲、角色、世界观、关系、时间线候选"},
        {"step": 7, "name": "save_candidates", "description": "保存候选并检查 status"},
        {"step": 8, "name": "apply_candidates", "description": "应用候选写入作品数据"},
        {"step": 9, "name": "verify_progress", "description": "验证本章已写入，再处理下一章"},
        {"step": 10, "name": "final_verify", "description": "全部章节完成后验证作品档案计数"},
    ]


def get_external_cataloging_forbidden_patterns() -> list[str]:
    return [
        "不要把中文小说档案改成英文或拼音",
        "不要调用需要墨枢 API 的内部 LLM 工具",
        "不要在工具 status != ok 后继续处理下一章",
        "不要报告完成除非 get_project_archive_status 验证通过",
        "不要跳过 apply_pending_cataloging",
        "不要跳过读写验证",
        "不要把角色当前状态字段拼接旧章节状态",
    ]
