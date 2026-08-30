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


def get_outline_granularity_rules() -> str:
    return """【大纲粒度统一规则】
1. 每章必须至少输出 1 条章级大纲候选，node_type 为 "chapter"，表示本章整体节点。章节已有绑定的大纲时，标题必须沿用该节点，系统会更新它的实际摘要，禁止另造同义章节点。
2. 本章存在多个重要场景、连续行动段、视角切换、冲突阶段或明显转折时，必须额外输出 2-6 条 node_type="section" 的 outline_create。
3. section 节点必须使用 parent_title 指向本章 chapter 节点；不要把 section 当成独立章节。
   不得把章节标题写进 parent_id，也不得自行猜测任何数据库 UUID；新节点只用 parent_title 表达父级，真实 ID 由司命解析。
4. chapter 节点 summary 写整章目标、冲突、转折、结果和结尾钩子；section 节点 summary 写该场景的地点、参与角色、行动目标、冲突推进、信息揭示和场景结果。
5. 如果章节非常短且只有单一场景，可以只输出 chapter 节点，但必须在 summary 中说明这是单场景章节。
6. section 节点尽量补充 scene_number、purpose、location、timeline、pov_character、characters、entry_state、exit_state、emotional_residue、unresolved_actions，供写后归档和上下文打包使用。
7. scene_number 是同一章节内的稳定场景键。重新建档时，同一逻辑场景沿用原编号；正文已删除的场景不要继续输出，系统会退役旧的建档场景节点。
8. 内部建档、外部 MCP 建档、本机 CLI 建档都必须遵守同一套大纲粒度规则；不要因为调用方式不同降低粒度。"""


def get_fact_extraction_rules() -> str:
    return """【事实抽取统一规则】
1. 只裸读当前章节正文，不读取旧角色卡、旧世界观和旧大纲。
2. 只抽取会影响大纲、角色、关系、世界观或后续写作连续性的事实，不复述普通动作流水账。
3. 只输出 JSONL；每一行是一个完整 JSON 对象；不要输出 Markdown、解释、代码块或 JSON 数组。
4. 字符串里的换行、引号、反斜杠必须正确转义。
5. 不做最终写入决策，不输出 character_create、worldbuilding_create、outline_create 等候选类型。
6. 只根据本章正文抽取事实；不确定的信息写入 uncertainty 字段，不要强行定论。

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
1. chapter_overview 必须输出 1 条。
2. character_fact.payload 尽量包含 names、primary_name、aliases、role_hint、age、actions、state_changes、appearance_clues、background_clues、location、realm_or_level、physical_state、mental_state、goals、items_or_assets、keywords；正文若揭示稳定人设，还要写 profile_clues，其中可含 core_motivation、inner_lack、core_belief、public_persona、hidden_persona、reveal_chapter、moral_taboo、voice、action_habit、trauma_trigger。不要为了填字段推测。
3. worldbuilding_fact.payload 尽量包含 title_hint、dimension_hint、keywords、content_points、rules、limits、affected_characters。
4. identity_hint.payload 必须包含 names、reason、evidence_points、confidence_reason。疑似同一人但未实锤也要输出，供下一阶段读取相关卡片。
5. outline_fact.payload 包含 title_hint、node_type、summary、characters、hook。
6. outline_fact 要覆盖整章节点和重要场景节点；有多个场景时要分别抽取 outline_fact，供候选阶段生成 section 节点。
7. 正文明示亲属、师徒、盟友、敌对、主从、合作或情感关系，或既有关系发生变化时，必须另输出 relationship_fact，不得只塞进 character_fact。relationship_fact.payload 必须包含 source_name、target_name、relationship_type、description；端点必须是角色，不能是地点、功法、组织或事件。
8. chapter_overview.payload 除 summary、key_events、scenes 外，尽量列出 characters 和 worldbuilding_titles，作为本章实体盘点；没有时写 []。
9. evidence 写短依据，payload 用短语和数组表达，不要复制大段原文。"""


def get_cataloging_candidate_schema() -> str:
    return """【允许的候选 type 与 payload】
首次生成回合的首个响应对象必须同时包含两个必填对象：
{"chapter_summary":{"summary_text":"...","coverage_manifest":{"scene_count":1,"characters":[],"worldbuilding":[],"relationships":[],"character_profiles":[]},"narrative_state":{"events":[],"timeline_events":[],"foreshadowing_planted":[],"foreshadowing_resolved":[],"storyline_progress":[],"new_storylines":[],"reader_known_facts":[],"character_known_facts":[],"unresolved_actions":[]},"narrative_review":{"source":"provided","outcome":"assessed"}},"chapter_outline":{"title":"当前章节原题","summary":"...","node_type":"chapter","status":"completed"}}
系统会把这两个必填对象拆成 chapter_summary 与章级 outline_create。增量修复回合不重复这个骨架，只输出校验明确指出的缺失或失败候选；chapter_summary 或 chapter_outline 只有在明确缺失时才补。没有角色、新设定或关系时，coverage_manifest 对应项必须是 []，不得虚构补卡。
除首次生成回合的首个必填骨架对象外，每一行顶层都必须包含标准字段 type，例如 {"type":"worldbuilding_create", ...}。type 决定候选类别；不要把候选类别写进 node_type。
node_type 只用于 outline_create/outline_update 的层级，而且只能是 chapter、section 或 volume；世界观、角色等候选不得输出 node_type。
- chapter_summary: {"summary_text":"...", "key_events":["..."], "characters":["..."], "worldbuilding":["..."], "coverage_manifest":{"scene_count":1,"characters":["..."],"worldbuilding":["..."],"relationships":[{"source_name":"...","target_name":"...","relationship_type":"..."}],"character_profiles":["本章新建或稳定档案发生变化的角色名"]}, "outline_hint":"...", "narrative_state":{"events":[...], "timeline_events":[...], "foreshadowing_planted":[...], "foreshadowing_resolved":[...], "storyline_progress":[...], "new_storylines":[...], "reader_known_facts":[...], "character_known_facts":[...], "unresolved_actions":[...]}, "narrative_review":{"source":"provided", "outcome":"assessed", "evidence":"本章叙事治理检查依据"}}
- outline_create / outline_update: {"title":"...", "summary":"...", "actual_summary":"...", "planned_summary":"...", "node_type":"chapter|section|volume", "parent_title":"...", "status":"completed", "related_characters":["..."], "scene_number":1, "purpose":"...", "location":"...", "timeline":"...", "pov_character":"...", "characters":["..."], "entry_state":"...", "exit_state":"...", "emotional_residue":"...", "unresolved_actions":[...]}
- character_create / character_update: {"name":"...", "aliases":["..."], "role_type":"protagonist|supporting|antagonist|mentor|other", "age":"...", "appearance":"...", "personality":"...", "background":"...", "abilities":["..."], "profile":{"core_motivation":"...","inner_lack":"...","core_belief":"...","public_persona":"...","hidden_persona":"...","reveal_chapter":3,"moral_taboo":"...","voice":"...","action_habit":"...","trauma_trigger":"..."}, "tone_style":"...", "catchphrases":["..."], "verbosity":"brief|moderate|verbose", "emotion_tendency":"...", "custom_system_prompt":"..."}
- character_state_update: {"name":"...", "aliases":["..."], "appearance":"...", "age":"...", "life_status":"alive|dead|unknown", "current_location":"...", "realm_or_level":"...", "physical_state":"...", "mental_state":"...", "current_goal":"...", "active_conflict":"...", "abilities_state":"...", "items_or_assets":"..."}
- character_timeline: {"name":"...", "event_description":"...", "event_type":"appearance|decision|injury|breakthrough|relationship_change|conflict|death|status_change|key_event", "emotional_state_change":"..."}
- character_relationship: {"source_name":"...", "target_name":"...", "relationship_type":"...", "description":"..."}
- character_merge_candidate: {"primary_name":"...", "secondary_name":"...", "canonical_name":"...", "aliases":["..."], "confidence_reason":"...", "evidence_points":["..."], "background_append":"..."}
- worldbuilding_create / worldbuilding_update: {"dimension":"geography|history|factions|power_system|races|culture", "title":"...", "content":"...", "status":"active"}
- worldbuilding_timeline: {"title":"...", "dimension":"...", "event_description":"...", "event_type":"introduced|confirmed|changed|damaged|used|limited", "evidence":"..."}
- chapter_link: {"character_names":["..."], "worldbuilding_titles":["..."], "outline_title":"...", "description":"...", "locations":["..."], "items":["..."], "events":["..."], "importance":"major|normal|minor", "appearance_order":1}"""


def get_cataloging_candidate_rules() -> str:
    return """【候选写入规则】
1. 每章必须至少生成 1 条 chapter_summary 和 1 条 chapter 级 outline_create。
   首次生成回合的第一行必须使用上述 chapter_summary + chapter_outline 必填响应对象；不能只返回其中一个，也不能先只返回摘要再提前结束。增量修复回合只补校验明确指出的缺失或失败候选，不得重复已通过候选。
   chapter_summary 必须包含非空 summary_text，用一段话概括本章已经发生的主要事件；同时显式包含完整 narrative_state，没有发现时也要保留所有数组并填写 []，并提供 narrative_review，不能用“字段缺失”表示“没有问题”。
   foreshadowing_planted、storyline_progress、unresolved_actions 中每个治理条目的 evidence 必须填写当前章节中可逐字检索的 6-120 字原文摘录，禁止用概述替代；找不到原文摘录时不要生成该条目。foreshadowing_resolved 等解决项必须携带已有治理项的 resolves_item_id 或 resolves_dedupe_key；找不到稳定引用时只报告待复核，不得按标题猜测并关闭旧记录。
   coverage_manifest 必须显式包含 scene_count、characters、worldbuilding、relationships、character_profiles 五项；空项也写 []。relationships 列出本章明确出现、确认或改变且影响后续连续性的角色关系，character_profiles 列出本章需要新建或更新稳定档案的角色。
   大纲粒度必须遵守【大纲粒度统一规则】：有多个重要场景时，除了 chapter 节点，还要输出 2-6 条 node_type="section" 的 outline_create，并用 parent_title 指向本章 chapter 节点。

2. 每个出场角色，必须输出 character_state_update 和 character_update（除非是全新角色用 character_create）。
   这是两个不同的候选类型，都要输出：

   character_state_update — 当前状态（逐章覆盖）：
   必须包含：appearance、age、life_status、current_location、realm_or_level、physical_state、mental_state、current_goal、active_conflict、abilities_state、items_or_assets。
   每章出场角色都要输出，即使没有变化也要输出当前值。

   character_update — 角色档案（有新信息就输出）：
   包含：name、aliases、role_type、personality、background、abilities、profile、tone_style、catchphrases、verbosity、emotion_tendency、custom_system_prompt。
   profile 是角色卡“稳定写作锁”，只依据正文和已有档案合并已知信息：core_motivation、inner_lack、core_belief、public_persona、hidden_persona、reveal_chapter、moral_taboo、voice、action_habit、trauma_trigger。新角色必须尽量补齐；已有角色在本章揭示或改变这些信息时必须更新。不得为了填满字段编造正文不存在的事实。
   ⚠️ background 和 custom_system_prompt 必须每次都是重写合并后的完整版本：
   - 读取已有角色档案，把本章新经历整合进已有背景，输出完整的 background。
   - 不要只写”本章新增：xxx”，要写”角色名，身份xxx，曾经历xxx，本章又xxx”。
   - custom_system_prompt 也要输出可直接替换旧提示词的完整版本，不要输出增量片段。
   - aliases 要包含所有已知称呼（本章新发现的 + 之前已有的）。
   如果本章没有任何新的角色信息（只是出场但没揭示新内容），可以不输出 character_update。

4. age 是描述性文本，不是精确数字。示例：”3岁”、”约16岁”、”外表约16岁，实际经历约200年”、”年龄不详”。

5. character_create 用于新角色，尽量包含 name、aliases、role_type、appearance、personality、background、abilities、profile、tone_style、catchphrases、verbosity、emotion_tendency、custom_system_prompt；role_type 只能填写 protagonist、supporting、antagonist、mentor、other 中的一个，身份、年龄等描述必须放在其他字段，不得拼进 role_type；并把该角色写入 coverage_manifest.character_profiles。

6. 角色有多个称呼时，name 放最稳定主名，aliases 放亲属称呼、尊称、昵称、身份名、化名。发现两个卡片其实是同一人时，输出 character_merge_candidate。

7. 世界观 dimension 必须使用 geography、history、factions、power_system、races、culture。修炼体系、阵法、病毒、封印优先 power_system；宗门/家族/组织优先 factions；地点优先 geography，不要全塞进 culture。

8. 新设定或设定变化要写 worldbuilding_create/update；设定被验证、破坏、限制或使用，写 worldbuilding_timeline。

9. 章节涉及的角色、世界观、大纲必须用 chapter_link 或对应摘要字段建立关联。

10. 角色关系不能只在自然语言摘要里一带而过。亲属、师徒、盟友、敌对、主从、利益合作、情感关系等，只要正文明确确认、首次出现或发生变化且会影响后续写作，就必须同时：
    - 写入 coverage_manifest.relationships（source_name、target_name、relationship_type）；
    - 输出一张同身份的 character_relationship 候选，description 写清依据和本章表现；
    - 关系双方都列入 coverage_manifest.characters，并已有角色档案或在本章先输出 character_create/update。
    不得用地点、功法、组织、事件充当关系端点，也不得靠 character_relationship 顺带创建空白角色卡。

11. 每次建档都是当前已保存章节版本的完整投影，不是向旧投影追加一份副本：
    - 同名角色、设定、关系、章级大纲和同 scene_number 场景表示更新；确实不存在时才创建。
    - 章节摘要、实际大纲摘要、章节关联和时间线写当前版本的完整值；不要保留正文已经删掉的旧事件。
    - 不得为了表达“正文有修改”而给实体或场景改一个近义标题；稳定主名、设定标题和场景编号必须沿用。"""


def get_incremental_cataloging_repair_rules() -> str:
    return """【候选缺项自动修复】
1. 当用户消息包含“上一轮校验未通过”时，这是增量修复回合，本节规则优先于首次生成规则。系统已经保留上一轮通过的候选；只输出错误信息明确指出的缺失候选，或解析失败、身份不一致、结构错误候选的修正版。
2. 不要重发完整候选集，不得重复、删除、缩减或改写已有正确候选。chapter_summary 和 chapter_outline 只有在错误明确指出其缺失时才允许输出。
3. 已有 coverage_manifest 是本章的累计验收合同。增量修复不得减少 scene_count，也不得删除其中已有角色、设定、关系或角色档案；需要修正清单时只能补充遗漏项。
4. 错误若列出缺失场景编号，逐个输出对应 scene_number 的 section outline_create，并填写 purpose、location、timeline、pov_character、characters、entry_state、exit_state、emotional_residue、unresolved_actions；不要用重写摘要代替场景卡。
5. 错误若列出缺失角色、设定、关系或章节关联，逐个输出相同稳定主名/标题/关系端点的对应候选。别名只放 aliases，不得在主名与别名之间切换。
6. 输出完成后立即结束，不要附带解释。"""


def get_candidate_resolution_rules() -> str:
    return "\n\n".join([
        """【候选生成统一规则】
1. 你会收到当前章节事实 JSONL，以及系统按事实检索出的相关角色、世界观、大纲、关系和索引。
2. 任务是把“新事实 + 相关旧资料”合并成可写入数据库的候选项，不要重新写读后感。
3. 只输出 JSONL；每一行是一个完整 JSON 对象；不要输出 Markdown、解释、代码块或 JSON 数组。
4. 首次生成回合的第一行必须按【允许的候选 type 与 payload】一次性输出 chapter_summary + chapter_outline 必填骨架；其余候选单独成行。增量修复回合只输出校验明确指出的缺失或失败候选。
   除首次生成回合的首个必填骨架外，禁止输出包含 character_state_updates、worldbuilding_entries、outline_creates、chapter_links 等列表的聚合对象。
5. 首次生成回合必须输出 1 条 chapter_summary，并包含非空 summary_text；不得只返回摘要后结束。增量修复时，已通过的 chapter_summary 不得重复输出。
6. 根据事实和相关卡片判断是创建、更新、关联还是提出角色合并候选。
7. payload 要足够写库但不冗长；不要重复粘贴旧资料，不要输出无变化字段。""",
        get_outline_granularity_rules(),
        get_cataloging_candidate_rules(),
        get_incremental_cataloging_repair_rules(),
    ])


def get_external_no_api_rules() -> str:
    from .prompt_source import get_api_free_mode_rules
    return get_api_free_mode_rules() + """

【编目专用补充规则】
1. 使用与内部建档一致的质量工具链：facts -> candidates -> apply -> verify。
2. facts 阶段只读当前章节正文并调用 save_external_cataloging_facts；不得读取旧档案或提前做创建、更新决策。
3. candidates 阶段调用 list_cataloging_facts，再读取当前档案镜像，把事实解析成可写候选并调用 save_external_cataloging_candidates。
4. 准备 candidates 时保持 JSONL 颗粒度：一条候选对应一个对象；不要把整章合成一个大对象。

【顺序规则】
- 建档必须逐章串行：phase="facts" → save_external_cataloging_facts → phase="candidates" → list_cataloging_facts → save_external_cataloging_candidates → apply_pending_cataloging → verify_external_cataloging_progress。
- 禁止并行处理后续章节；前一章创建/更新的角色、世界观和大纲会影响后一章应使用 create 还是 update。
- 每章必须完成 apply_pending_cataloging 后才能处理下一章。
- 候选只是暂存，不应用就不会出现在角色、大纲、世界观、章节摘要里。

推荐工作流：
1. 调用 start_external_cataloging_job 创建任务。
2. 调用 get_next_external_cataloging_chapter(phase="facts")，裸读返回的最早未完成章节，保存规范事实。
3. 调用 get_next_external_cataloging_chapter(phase="candidates") 和 list_cataloging_facts，读取与事实有关的档案镜像，生成完整候选并保存。
4. apply_pending_cataloging 后立刻 verify_external_cataloging_progress；验证完成后再处理下一章。
5. 最终 verify_external_cataloging_progress + get_project_archive_status 验证。"""


def get_internal_cataloging_system_prompt() -> str:
    return "\n\n".join([
        "你是“作品建档”初始化抽取器。目标不是写读后感，而是把单章正文拆成可长期用于写作助手的结构化资料：章节摘要、大纲节点、角色档案、角色状态、角色关系、世界观设定和时间线。",
        "硬性输出规则：只输出 JSONL；每一行必须是一个完整 JSON 对象；不要输出 Markdown、解释、代码块或 JSON 数组。每条信息一行，不要为了省行数合并重要信息。",
        get_language_rules(),
        get_outline_granularity_rules(),
        get_cataloging_candidate_rules(),
        get_incremental_cataloging_repair_rules(),
        get_time_tracking_rules(),
        get_naming_resolution_rules(),
        get_cataloging_candidate_schema(),
    ])


def get_external_cataloging_system_prompt() -> str:
    return "\n\n".join([
        "你是一个外部编目 Agent。你的任务是在不调用司命内部模型 API 的情况下，对导入的小说项目进行编目：提取角色、世界观、大纲和章节摘要，并通过司命工具保存到正确作品。",
        get_project_binding_rules(),
        get_language_rules(),
        get_external_no_api_rules(),
        get_fact_extraction_rules(),
        get_outline_granularity_rules(),
        get_cataloging_candidate_rules(),
        get_incremental_cataloging_repair_rules(),
        get_time_tracking_rules(),
        get_naming_resolution_rules(),
        get_cataloging_candidate_schema(),
        get_candidate_format_examples(),
        get_merge_rules(),
        get_completion_criteria(),
    ])


def get_candidate_format_examples() -> str:
    return """【候选类型格式】
save_external_cataloging_candidates 的 candidates 数组中，每个候选的格式：

1. 章节摘要（尽量详细，不要只写一句话）：
{“type”: “chapter_summary”, “summary”: “详细摘要，包含本章目标、冲突、关键转折、结尾钩子、涉及角色，至少200字”}

2. 大纲节点（summary 要写清楚：本章目标、冲突、关键转折、结尾钩子、涉及角色）：
{“type”: “outline_create”, “title”: “第一章 穿越”, “node_type”: “chapter”, “summary”: “张三穿越到修仙世界，发现自己是废柴体质，但意外获得神秘功法。冲突是身份暴露的风险，转折是发现功法来源，结尾钩子是有人在追查他。”, “related_characters”: [“张三”]}

2.1 大纲场景节点（本章有多个重要场景时必须输出 2-6 条，parent_title 指向本章 chapter 节点）：
{“type”: “outline_create”, “title”: “第一章 穿越 / 石狮异动”, “node_type”: “section”, “parent_title”: “第一章 穿越”, “summary”: “陆家院内，张三观察石狮眉心异动，确认这不是普通装饰，而是后续阵法线索。场景目标是建立异常感知，冲突是信息不足，结果是埋下石狮伏笔。”, “related_characters”: [“张三”]}

3. 新角色（必须用 character_create，所有字段都要尽量填写完整）：
重要：appearance、personality、background、abilities 都必须详细描写，不要只写一两个词。
background 必须是完整的背景档案，不是本章新增片段。
{“type”: “character_create”, “name”: “特昂糖”, “aliases”: [“糖糖”, “陆糖”], “role_type”: “protagonist”, “age”: “3岁”, “appearance”: “3岁幼女，矮小但步伐稳健，眼神中带着不属于这个年龄的冷静与洞察”, “personality”: “冷静理性、分析能力强、成熟超越年龄、偶尔流露前世成人的思维方式”, “background”: “前世是华清实验室神经网络研究员，姚班天才少女。穿越到修仙世界成为陆家旁支幼女。拥有前世记忆和科学思维，能用数据分析方法理解修炼体系。”, “abilities”: [“感知灵气波动”, “优化修炼路径”, “数据分析”], “tone_style”: “简洁冷静，偶尔用科学术语”, “catchphrases”: “数据不会说谎”, “emotion_tendency”: “表面冷静内心温暖”, “custom_system_prompt”: “你是特昂糖，3岁幼女身体里住着一个成年科学家的灵魂。你用数据分析的方式理解修仙世界，说话简洁但精准。你关心家人但不善表达。你有强烈的求知欲和探索精神。在危险面前你保持冷静分析，但内心深处害怕失去来之不易的家人。300-800字，包含身份、已知经历、性格动机、说话方式、当前立场、关系网、行动边界和禁止违背的设定。”}

4. 角色状态更新（每个出场角色都必须输出！用 character_state_update）：
appearance 和 age 也是当前状态，必须包含。
{“type”: “character_state_update”, “name”: “特昂糖”, “appearance”: “3岁幼女，左臂缠着绷带（本章受伤）”, “age”: “3岁”, “current_location”: “陆家后院”, “current_goal”: “找到回家的方法”, “life_status”: “alive”, “physical_state”: “左臂受伤，行动受限”, “mental_state”: “冷静分析中带着迷茫”, “active_conflict”: “身份暴露的风险”, “realm_or_level”: “未修炼”, “abilities_state”: “感知灵气波动”, “items_or_assets”: “无”}

5. 角色档案更新（有新信息时必须输出！用 character_update，与 character_state_update 是两个不同的候选）：
background 必须是完整重写，不是追加。custom_system_prompt 也要完整替换。
{“type”: “character_update”, “name”: “特昂糖”, “aliases”: [“糖糖”, “陆糖”, “陆家小妹”], “personality”: “冷静理性、分析能力强、本章展现出对哥哥的依赖和信任”, “background”: “前世是华清实验室神经网络研究员，姚班天才少女。穿越到修仙世界成为陆家旁支幼女。拥有前世记忆和科学思维。本章中遭遇周氏袭击，左臂受伤，被哥哥陆景珩救下，从此更加信任哥哥。”, “custom_system_prompt”: “你是特昂糖，3岁幼女身体里住着一个成年科学家的灵魂...（完整300-800字）”}

5. 世界观条目（content 必须具体：定义、规则、限制、代价、来源、影响范围、与角色/剧情的关系）：
{“type”: “worldbuilding_create”, “title”: “护族大阵”, “dimension”: “power_system”, “content”: “陆家祖传防护阵法，由历代家主灵力维持。激活需要消耗大量灵石，可抵御筑基期以下攻击。阵法核心在祖祠地下，与陆家血脉绑定。本章中被旁支周氏暗中破坏了东侧节点。”}

6. 角色关系（描述要说明关系的来源和表现）：
{“type”: “character_relationship”, “source_name”: “陆景珩”, “target_name”: “特昂糖”, “relationship_type”: “兄妹”, “description”: “陆景珩是特昂糖的哥哥，对她保护有加。在修炼中主动帮妹妹挡危险，教她基础吐纳法。”}

重要规则：
- character_create 的 name 字段是必填的
- character_state_update 用于更新角色当前状态（位置、目标等），不是创建新角色
- character_update 用于更新角色基本信息（外貌、性格等），需要 name 字段
- 不要使用 new_character、new_worldbuilding 等非标准类型
- 所有字段都要尽量详细，不要只写一两个词
- background 必须是完整背景，不是增量补丁
- custom_system_prompt 要写300-800字，帮助AI扮演该角色"""


def get_merge_rules() -> str:
    return """【合并规则】
- 角色别名：如果同一角色有多个名字，使用主名字作为规范名
- 角色当前状态字段：覆盖旧状态
- 角色背景、外貌、custom_system_prompt：重写合并，不做简单追加；合并后输出可直接替换旧字段的完整版本
- 世界观：相同标题的条目进行语义合并，不创建重复
- 大纲：每章创建一个新节点，除非明确对应现有节点"""


def get_completion_criteria() -> str:
    return """【工具返回契约】
每次工具调用后必须读取返回 JSON 的 status：
- status == “ok”：继续下一步。
- status != “ok”：立即停止，报告失败工具、status、detail，不要继续下一章，不要说完成。
写入后必须用新的查询验证，不要用缓存结果代替验证。

【完成标准】
最终调用 get_project_archive_status，且确认数据属于目标 project_id。通常应满足：
- chapters_count > 0
- chapter_summaries_count > 0
- outline_nodes_count > 0
- characters_count > 0
- worldbuilding_count > 0（类型小说通常应有）
- warnings 为空或已解释并处理
不满足时只能说”尚未完成”，并给出下一步。"""


def get_external_cataloging_workflow() -> list[dict[str, object]]:
    return [
        {"step": 1, "name": "select_project", "description": "导入或选择作品，记录 project_id"},
        {"step": 2, "name": "start_job", "description": "使用 project_id 创建外部无 API 建档任务"},
        {"step": 3, "name": "extract_facts", "description": "领取 phase='facts'：只读当前章 → save_external_cataloging_facts", "parallel": False},
        {"step": 4, "name": "resolve_candidates", "description": "领取 phase='candidates'：读取已保存事实与当前档案 → save_external_cataloging_candidates", "parallel": False},
        {"step": 5, "name": "apply_and_verify", "description": "apply_pending_cataloging → verify_external_cataloging_progress → 再处理下一章", "parallel": False},
        {"step": 6, "name": "final_verify", "description": "verify_external_cataloging_progress + get_project_archive_status 验证作品档案计数"},
    ]


def get_external_cataloging_forbidden_patterns() -> list[str]:
    return [
        "不要把中文小说档案改成英文或拼音",
        "不要调用需要司命 API 的内部 LLM 工具",
        "不要在工具 status != ok 后继续处理下一章",
        "不要报告完成除非 get_project_archive_status 验证通过",
        "不要跳过 apply_pending_cataloging",
        "不要跳过读写验证",
        "不要把角色当前状态字段拼接旧章节状态",
    ]
