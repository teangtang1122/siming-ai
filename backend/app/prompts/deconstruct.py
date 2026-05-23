"""Prompt templates for deconstruct / book analysis map-reduce pipeline."""

# ── Map phase ────────────────────────────────────────────────────────

MAP_OUTPUT_RULES = (
    "\n\n【JSON输出硬性规范】\n"
    "1. 只输出 minified JSON：不要换行排版，不要 Markdown，不要解释文字。\n"
    "2. 输出必须以 { 开头，以 } 结束，最后一个字符必须是右花括号。\n"
    "3. 顶层字段必须完整，字段名必须使用模板中的英文名，不得新增中文字段名。\n"
    "4. 这是事实卡片，不是最终档案。只记录可用于后续合并的事实，不要写长篇角色档案、完整大纲或长世界观条目。\n"
    "5. 数量上限：characters最多9个，events最多9个，world_facts最多6个，clues最多5个，themes最多6个，techniques最多6个。\n"
    "6. 长度上限：fact/appearance 每项最多120个中文字符，role_hint/speech_style 最多60个中文字符，relationships/appearances 数组每项最多60字。\n"
    "7. 如果内容太多，优先保留主线事件、角色变化、冲突转折、设定规则和伏笔线索。\n"
    "8. 整个JSON输出必须紧凑完整，不得在字符串或数组中间截断。宁可少写几条也要保证JSON完整闭合。\n"
)

MAP_SYSTEM_PROMPT = (
    "你是一位小说拆书流水线中的分块事实提取器。你的任务不是写最终报告，而是从当前文本片段中提取短小、准确、可合并的事实卡片。\n"
    "后续会有更强模型基于这些事实卡片生成角色档案、大纲和世界观，所以你必须保持输出短、准、结构稳定。\n\n"
    "【核心要求】\n"
    "1. 必须只输出一个合法JSON对象——不要输出Markdown代码块、解释文字、前缀或后缀。\n"
    "2. 角色名必须使用原文姓名；事件必须写清谁做了什么、造成什么结果。\n"
    "3. 如果本段出现外貌、衣着、年龄、体态、神态、说话习惯、关系变化或出场行为，只记录为短字符串，不要展开成嵌套长档案。\n"
    "4. 只提取事实，不扩写、不评价、不生成最终档案。没有信息时使用空数组 [] 或空字符串 ''，但不得省略顶层字段。\n"
    "5. JSON必须可被 json.loads 直接解析，不得包含尾随逗号、注释或非标准语法。\n"
    "6. 宁可输出空数组或少写几条，也必须保证JSON以 } 完整闭合。截断的JSON会导致整段作废。\n\n"
    "【禁止事项】\n"
    "- 禁止用「主角」「父亲」「老人」「少女」等代称代替角色姓名——必须从原文中提取实际姓名。\n"
    "- 禁止使用「神秘力量」「性格复杂」「实力强大」等空洞描述——必须具体说明是什么力量、什么性格特征、如何强大。\n"
    "- 禁止输出 character_profiles、outline_hints、worldbuilding_entries、golden_three_signals 等最终报告字段。\n"
    "- 禁止在 JSON 外输出任何内容。"
)

MAP_JSON_TEMPLATE = (
    '{"characters":[{"name":"","role_hint":"","mentions":0,"facts":[""],"appearance":"","speech_style":"","relationships":[""],"appearances":[""]}],'
    '"events":[{"summary":"","type":"intro|conflict|reveal|turn|climax|resolution|setup|other","characters":[""],"importance":"high|medium|low"}],'
    '"world_facts":[{"dimension":"geography|history|factions|power_system|races|culture","name":"","fact":""}],'
    '"clues":[{"item":"","detail":""}],'
    '"pacing":"slow|medium|fast|intense",'
    '"narrative_mode":"description|dialogue|action|reflection|exposition|mixed",'
    '"themes":[""],"techniques":[""]}'
)

JSON_REPAIR_SYSTEM_PROMPT = (
    "你是JSON修复器，只修复语法，不重新分析文本，不增删事实。"
    "输入是模型输出的近似JSON，可能有中文引号、漏引号、尾随逗号、截断字段或Markdown。"
    "你必须只返回一个可被 json.loads 解析的合法JSON对象。"
)


def map_instructions(options: dict) -> str:
    """Build map-phase instruction text for chunk analysis."""
    parts = [
        "【分块事实卡片要求】",
        "1. characters 只记录角色名、身份提示、本段事实短句、外貌/说话风格、关系短句和出场短句，不写完整档案。",
        "2. facts/relationships/appearances 都必须是短字符串数组，不要输出对象数组。",
        "3. world_facts 只记录明确设定事实，例如地点、势力、修炼规则、历史、文化、种族；没有就空数组。",
        "4. clues 记录伏笔、线索、未解谜团或后续可能回收的信息。",
        "5. themes 每条不超过8字；techniques 只写本段明确出现的写作手法。",
        "6. pacing 判断：slow=描写/思考为主；medium=事件推进与描写交替；fast=连续事件/对话驱动；intense=高潮战斗/重大揭示。",
        "7. events 只记录主线事件事实：summary 写谁做了什么以及结果，不确定的信息不要猜。",
        "8. 这是给合并模型看的事实原料，宁可短而准，不要长而散。",
    ]
    return "\n".join(parts)


# ── Reduce phase ─────────────────────────────────────────────────────

REDUCE_SYSTEM_PROMPT = (
    "你是一位资深网文编辑与拆书整合专家，专精于将分散的文本分析结果合成为一份完整、连贯、可直接导入创作系统的拆书报告。\n"
    "你的工作不是重新分析原文，而是将各分块的结果进行去重、补全、排序和润色，使其成为一份有机的整体。\n\n"
    "【整合原则】\n"
    "1. 角色去重与合并：同名角色（含名字相同但略有变体的情况）必须合并为一个条目，保留所有分块中最具体、最丰富的信息。同一个角色可能在不同分块中被多次提到，你只能输出一次。\n"
    "2. 信息优先级：当不同分块对同一角色的描述有冲突时，优先采用提及次数更多、描述更详细的分块；在备注中标注可能的歧义。\n"
    "3. 大纲层级组织：根据分块中的 events/clues/themes 信息，按卷→章→节的自然层级组织结构。相近的剧情节点合并为同一章，不要为每个零散事件单独建章。\n"
    "4. 世界观条目拆分：每个独立的概念（如一个地点、一条规则、一个势力）必须拆成独立的 worldbuilding 条目。不得将多个不相关的设定打包进一个条目。\n"
    "5. 字段补全：如果某个字段在所有分块中都为空，保留空值。但只要有分块提供了信息，就必须填入。\n\n"
    "【禁止事项】\n"
    "- 禁止重复输出同一角色——同一个角色在所有分块中只能出现一次。\n"
    "- 禁止输出与分块分析结果矛盾的信息——你的工作是整合而非编造。\n"
    "- 禁止将分块结果直接拼接而不做去重——如果同一事件在不同分块中被描述，必须合并或选择最优表述。\n"
    "- 禁止输出空泛的大纲——每个卷/章节点必须有具体的 summary，包含目标、冲突、行动、结果。\n"
    "- 禁止在 JSON 外输出任何内容。\n\n"
    "【质量判断】\n"
    "- 好的整合报告：读起来像是一份完整的作品档案——角色有血有肉、有关系网、有出场记录、有可供AI扮演角色的系统提示词；大纲层次分明且关联角色；世界观条目独立可查。\n"
    "- 失败的整合报告：角色重复出现、大纲条目碎片化、世界观条目互相包含而重复。"
)


def reduce_template(options: dict) -> str:
    """Build the reduce-phase JSON output template based on enabled modules."""
    optional_fields = []
    if options.get("golden_three"):
        optional_fields.append(
            '  "golden_three": {"hook":"","protagonist_goal":"","core_conflict":"","reader_expectation":"","chapter_1_function":"","chapter_2_function":"","chapter_3_function":"","problems":[""],"optimization_suggestions":[""]},'
        )
    if options.get("outline"):
        optional_fields.append(
            '  "structure": {"volumes": [{"title":"","summary":"","chapters": [{"title":"","summary":"","start_chunk":0,"conflict":"","turning_point":"","goal":"","outcome":"","hook":"","characters":[""],"character_roles":[{"name":"","role_in_scene":""}],"foreshadowing":[""]}]}],"total_estimated_chapters":0},'
        )
    else:
        optional_fields.append('  "structure": {"volumes": [],"total_estimated_chapters":0},')
    optional_fields.append(
        '  "plot_nodes": [{"description":"","type":"intro|development|turn|climax|resolution","position_pct":0,"importance":"high|medium|low"}],'
    )
    if options.get("characters"):
        optional_fields.append(
            '  "characters": [{"name":"","role":"protagonist|supporting|antagonist|mentor|other","role_type":"protagonist|supporting|antagonist|mentor|other","mention_count":0,"importance":"high|medium|low","appearance":"","appearance_source":"original|inferred|mixed|unknown","personality":"","background":"","abilities":[""],"arc_description":"","motivation":"","conflict":"","speech_style":"","relationship_network":[{"target_name":"","relationship_type":"","description":"","attitude":"","evidence":""}],"appearance_records":[{"chapter_title":"","source_chunk":0,"scene":"","role_in_scene":"","summary":""}],"timeline_events":[{"source_chunk":0,"event_type":"key_decision|relationship_change|skill_gain|injury|emotional_turning_point|other","description":"","emotional_state_change":""}],"ai_config":{"tone_style":"neutral|arrogant|gentle|cold|enthusiastic|mysterious|sarcastic|formal|casual|aggressive","catchphrases":[""],"verbosity":"brief|moderate|verbose","emotion_tendency":"neutral|optimistic|pessimistic|angry|calm|anxious|sad|excited","custom_system_prompt":""}}],'
        )
    else:
        optional_fields.append('  "characters": [],')
    if options.get("worldbuilding"):
        optional_fields.append(
            '  "worldbuilding_entries": [{"dimension":"geography|history|factions|power_system|races|culture","title":"","content":"","related_characters":[""],"plot_usage":"","constraints":[""]}],'
        )
    else:
        optional_fields.append('  "worldbuilding_entries": [],')
    optional_fields.append(
        '  "highlights": [{"type":"climax|reveal|emotional|action","description":"","position_pct":0,"intensity":"low|medium|high"}],'
    )
    optional_fields.append(
        '  "rhythm_curve": [{"position_pct":0,"pace":"slow|medium|fast|intense","label":""}],'
        if options.get("rhythm") else '  "rhythm_curve": [],'
    )
    optional_fields.append(
        '  "patterns": [{"type":"technique|theme|structure","description":"","frequency":"rare|moderate|frequent","examples":[""]}]'
        if options.get("patterns")
        else '  "patterns": []'
    )
    return "{\n" + "\n".join(optional_fields) + "\n}"


def reduce_instructions(options: dict) -> str:
    """Build reduce-phase integration instructions based on enabled modules."""
    instructions = [
        "【整合规则】",
        "1. 输入是分块事实卡片，不是最终报告。你要基于 characters/actions/events/world_facts/clues/themes/techniques 生成完整、可导入的角色档案、大纲和世界观。",
        "2. 角色去重：严格按 name 字段合并同名角色。合并 role_hint、actions、traits 和事件中的行为证据，补全 appearance/personality/background/abilities/ai_config；没有原文依据的外貌不要编造，留空或写未明确描写。",
        "3. 大纲组织：从 events 中提取事件线，按时间顺序组织为 卷（volume）→ 章（chapter）。每章 summary 必须包含目标、冲突、行动、结果、钩子。相近事件合并为同一章。",
        "4. 世界观拆分：从 world_facts 中抽取独立设定条目。一个地点、一条规则、一个势力、一段历史分别成条，不要写成总括段。",
        "5. 伏笔和高光：从 clues 与 events 中提炼 plot_nodes/highlights，保留重要揭示、转折、高潮和后续钩子。",
        "6. 节奏曲线（rhythm_curve）：综合各分块 pacing，标注舒缓、推进、高潮、转折位置。",
        "7. 写作模式（patterns）：综合 techniques/themes，总结反复出现的写法、主题和结构特点。",
        "8. 未启用模块：如果某个模块选项为关闭状态，对应字段输出空数组 [] 或空对象 {}，但不省略该字段本身。",
    ]
    if options.get("golden_three"):
        instructions.append(
            "\n【黄金三章模块】\n"
            "9. 黄金三章只能依据提示中单独提供的「前三章原文摘录」分析，不能用全书后文倒推开篇：\n"
            "- 评价开篇钩子的有效性。\n"
            "- 明确主角初始目标和核心冲突。\n"
            "- 分析前三章各自承担的功能和衔接效果。\n"
            "- 指出开篇存在的问题（如节奏拖沓、信息量过大、主角被动等）并给出优化建议。"
        )
    return "\n".join(instructions)


# ── Reduce per-section templates ─────────────────────────────────────

REDUCE_SECTION_TEMPLATES = {
    "outline": (
        '{"structure":{"volumes":[{"title":"","summary":"","chapters":[{"title":"","summary":"","start_chunk":0,"conflict":"","turning_point":"","goal":"","outcome":"","hook":"","characters":[""],"character_roles":[{"name":"","role_in_scene":""}],"foreshadowing":[""]}]}],"total_estimated_chapters":0},'
        '"plot_nodes":[{"description":"","type":"intro|development|turn|climax|resolution","position_pct":0,"importance":"high|medium|low"}],'
        '"highlights":[{"type":"climax|reveal|emotional|action","description":"","position_pct":0,"intensity":"low|medium|high"}]}'
    ),
    "characters": (
        '{"characters":[{"name":"","role":"protagonist|supporting|antagonist|mentor|other","role_type":"protagonist|supporting|antagonist|mentor|other","mention_count":0,"importance":"high|medium|low","appearance":"","appearance_source":"original|inferred|mixed|unknown","personality":"","background":"","abilities":[""],"arc_description":"","motivation":"","conflict":"","speech_style":"","relationship_network":[{"target_name":"","relationship_type":"","description":"","attitude":"","evidence":""}],"appearance_records":[{"chapter_title":"","source_chunk":0,"scene":"","role_in_scene":"","summary":""}],"timeline_events":[{"source_chunk":0,"event_type":"key_decision|relationship_change|skill_gain|injury|emotional_turning_point|other","description":"","emotional_state_change":""}],"ai_config":{"tone_style":"neutral|arrogant|gentle|cold|enthusiastic|mysterious|sarcastic|formal|casual|aggressive","catchphrases":[""],"verbosity":"brief|moderate|verbose","emotion_tendency":"neutral|optimistic|pessimistic|angry|calm|anxious|sad|excited","custom_system_prompt":""}}]}'
    ),
    "worldbuilding": (
        '{"worldbuilding_entries":[{"dimension":"geography|history|factions|power_system|races|culture","title":"","content":"","related_characters":[""],"plot_usage":"","constraints":[""]}]}'
    ),
    "rhythm_patterns": (
        '{"rhythm_curve":[{"position_pct":0,"pace":"slow|medium|fast|intense","label":""}],'
        '"patterns":[{"type":"technique|theme|structure","description":"","frequency":"rare|moderate|frequent","examples":[""]}]}'
    ),
    "golden_three": (
        '{"golden_three":{"hook":"","protagonist_goal":"","core_conflict":"","reader_expectation":"","chapter_1_function":"","chapter_2_function":"","chapter_3_function":"","problems":[""],"optimization_suggestions":[""]}}'
    ),
}

REDUCE_SECTION_INSTRUCTIONS = {
    "outline": "只生成大纲结构、关键情节节点和高光事件。每章 summary 至少包含目标、冲突、行动、结果、下一钩子，并填写 characters 与 character_roles，便于导入后关联角色。",
    "characters": "只生成角色档案。合并同名角色，补全外貌、性格、背景、能力、关系网、出场记录、时间线和 ai_config.custom_system_prompt。若原文没有明确外貌，可根据年龄/身份/气质/能力合理生成一段外貌，并把 appearance_source 标为 inferred；custom_system_prompt 必须是一段可直接让AI扮演该角色的系统提示词，包含身份、性格、说话习惯、禁忌、关系和当前动机。",
    "worldbuilding": "只生成世界观条目。从 world_facts 中拆出独立设定，一条规则、地点、势力或历史分别成条。",
    "rhythm_patterns": "只生成节奏曲线和写作模式。综合 pacing/themes/techniques，不要输出角色或大纲。",
    "golden_three": "只分析黄金三章。只能依据前三章原文摘录，不能用后文倒推开篇。",
}

REDUCE_SECTION_LABELS = {
    "outline": "大纲与情节",
    "characters": "角色档案",
    "worldbuilding": "世界观",
    "rhythm_patterns": "节奏与模式",
    "golden_three": "黄金三章",
}
