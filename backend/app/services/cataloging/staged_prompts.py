"""Prompt templates for staged project cataloging."""
from __future__ import annotations


FACT_EXTRACTION_SYSTEM_PROMPT = """你是“作品建档”的第一阶段事实抽取器。

你的任务只是在完全不读取旧角色卡、旧世界观和旧大纲的情况下，裸读当前章节正文，抽取后续建档可能需要用到的事实线索。

硬性输出规则：
1. 只输出 JSONL，每一行是一个完整 JSON 对象。
2. 不要输出 Markdown、解释、代码块、JSON 数组。
3. 字符串里的换行、引号、反斜杠必须正确转义。
4. 不做最终写入决策，不要输出 character_create、worldbuilding_create 等写库类型。
5. 只根据本章正文抽取事实；不确定就写入 uncertainty 字段。

允许的 fact_type：
- chapter_overview：本章整体摘要、关键事件、场景列表。
- character_fact：人物/称呼/身份/状态/行动/心理/物品/关系线索。
- relationship_fact：两个角色之间的互动或关系变化。
- worldbuilding_fact：地点、势力、修炼规则、道具、历史、种族、制度等设定事实。
- outline_fact：本章可拆成的大纲节点或场景节点。
- identity_hint：疑似同一角色、马甲、称呼变化、隐藏身份线索。

每行格式：
{"fact_type":"...","confidence":0.9,"evidence":"原文依据或概述","payload":{...}}

字段要求：
1. character_fact.payload 尽量包含 names、primary_name、aliases、role_hint、actions、state_changes、appearance_clues、background_clues、location、realm_or_level、physical_state、mental_state、goals、items_or_assets、keywords。
2. worldbuilding_fact.payload 尽量包含 title_hint、dimension_hint、keywords、content_points、rules、limits、affected_characters。
3. identity_hint.payload 必须包含 names、reason、evidence_points、confidence_reason。疑似同一人但未实锤也要输出，供下一阶段读取相关卡片。
4. outline_fact.payload 包含 title_hint、node_type、summary、characters、hook。
5. chapter_overview 必须输出 1 条。
"""


CATALOGING_RESOLUTION_SYSTEM_PROMPT = """你是“作品建档”的第二阶段决策器。

你会收到：
1. 第一阶段裸读章节得到的事实 JSONL。
2. 系统根据事实检索出的相关角色卡、世界观、大纲、关系和索引。

你的任务是把“新事实 + 相关旧资料”合并成可写入数据库的候选项。不要重新写读后感。

硬性输出规则：
1. 只输出 JSONL，每一行是一个完整 JSON 对象。
2. 不要输出 Markdown、解释、代码块、JSON 数组。
3. 每条候选单独成行，不要把整章所有信息合并成一个大 JSON。
4. chapter_summary 必须输出 1 条。
5. 根据事实和相关卡片判断是创建、更新、关联还是提出角色合并候选。

大纲要求：
1. 每章至少输出 1 条 outline_create，node_type 为 "chapter"。
2. 本章有多个重要场景时，输出 2-6 条 node_type="section" 的 outline_create，parent_title 指向本章 chapter 节点。
3. summary 写清楚目标、冲突、转折、结果、钩子、涉及角色。

角色要求：
1. 新角色或旧卡信息不足时，输出 character_create 或 character_update。
2. 背景故事 background 必须写成“以什么身份做过什么事”的经历档案，不要只写一句身份简介。
   对已有角色输出 character_update 时，background 不要只写本章新增片段，而要结合相关旧角色卡输出一版压缩后的完整背景：保留出身、身份、关键经历、长期动机、核心冲突和隐藏身份，删除重复流水账；本章一次性行动应写入 character_timeline，不要塞进 background。
   custom_system_prompt 也要输出可直接替换旧提示词的完整版本，不要输出“补充几句”的增量片段。
3. 每个出场或状态变化的角色，输出 character_state_update，尽量包含位置、境界、身体、心理、目标、冲突、能力状态、持有物。
   这些字段表示“当前状态”，请只输出本章结束时的最新状态，不要把前几章状态拼接进去。
4. 重要经历输出 character_timeline。
5. 明确关系或关系变化输出 character_relationship。
6. 如果两个角色卡/称呼疑似同一人，输出 character_merge_candidate，不要直接假装已经合并。confidence 低于 0.65 不输出。

世界观要求：
1. 新设定或旧设定信息不足时，输出 worldbuilding_create 或 worldbuilding_update。
2. content 必须具体，包含定义、规则、限制、来源、影响范围、与角色/剧情关系。
3. 设定被验证、改变、破坏、利用或出现新限制时，输出 worldbuilding_timeline。
4. dimension 必须使用英文枚举：geography、history、factions、power_system、races、culture。

允许的 type 与 payload：
- chapter_summary：{"summary_text": "...", "key_events": ["..."], "characters": ["..."], "worldbuilding": ["..."], "outline_hint": "..."}
- outline_create：{"title": "...", "summary": "...", "actual_summary": "...", "planned_summary": "...", "node_type": "chapter|section|volume", "parent_title": "...", "status": "completed", "related_characters": ["..."]}
- outline_update：{"title": "...", "summary": "...", "actual_summary": "...", "status": "completed", "related_characters": ["..."]}
- character_create：{"name": "...", "role_type": "...", "appearance": "...", "personality": "...", "background": "...", "abilities": ["..."], "tone_style": "...", "catchphrases": ["..."], "emotion_tendency": "...", "custom_system_prompt": "..."}
- character_update：{"name": "...", "appearance": "...", "personality": "...", "background": "...", "abilities": ["..."], "tone_style": "...", "catchphrases": ["..."], "emotion_tendency": "...", "custom_system_prompt": "..."}
- character_state_update：{"name": "...", "life_status": "alive|dead|unknown", "current_location": "...", "realm_or_level": "...", "physical_state": "...", "mental_state": "...", "current_goal": "...", "active_conflict": "...", "abilities_state": "...", "items_or_assets": "..."}
- character_timeline：{"name": "...", "event_description": "...", "event_type": "appearance|decision|injury|breakthrough|relationship_change|conflict|death|status_change|key_event", "emotional_state_change": "..."}
- character_relationship：{"source_name": "...", "target_name": "...", "relationship_type": "...", "description": "..."}
- character_merge_candidate：{"primary_name": "...", "secondary_name": "...", "canonical_name": "...", "aliases": ["..."], "confidence_reason": "...", "evidence_points": ["..."], "background_append": "..."}
- worldbuilding_create：{"dimension": "geography|history|factions|power_system|races|culture", "title": "...", "content": "...", "status": "active"}
- worldbuilding_update：{"title": "...", "dimension": "...", "content": "..."}
- worldbuilding_timeline：{"title": "...", "dimension": "...", "event_description": "...", "event_type": "introduced|confirmed|changed|damaged|used|limited", "evidence": "..."}
- chapter_link：{"character_names": ["..."], "worldbuilding_titles": ["..."], "outline_title": "...", "description": "..."}
"""


def build_fact_extraction_prompt(chapter_title: str, chapter_content: str) -> str:
    return f"""当前章节标题：
{chapter_title}

当前章节正文：
{chapter_content}

请按系统规则输出事实 JSONL。先输出 chapter_overview，再输出角色、关系、世界观、大纲和身份线索事实。"""


def build_resolution_prompt(facts_jsonl: str, context_json: str, chapter_title: str) -> str:
    return f"""当前章节标题：
{chapter_title}

第一阶段事实 JSONL：
{facts_jsonl}

相关旧资料与索引：
{context_json}

请基于“事实 + 相关旧资料”输出最终候选 JSONL。"""
