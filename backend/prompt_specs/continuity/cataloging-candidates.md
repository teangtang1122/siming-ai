---
id: continuity.cataloging.candidates
version: 3.1.21
scope: continuity
visibility: both
inputs: []
output_format: jsonl
tool_policy: none
tools:
  - inspect_story_granularity
  - repair_story_granularity
  - get_narrative_ledger
budget:
  fixed_chars: 5200
  context_chars: 80000
golden_cases:
  - name: required-granularity
    required_text: ["chapter_summary", "chapter_outline", "首个响应对象", "coverage_manifest", "relationships", "character_profiles", "character_state_update", "node_type=\"section\"", "chapter_link", "JSONL"]
  - name: incremental-repair
    required_text: ["增量修复回合", "保留上一轮", "缺失身份", "既有设定", "身份未确认"]
  - name: narrative-ledger
    required_text: ["narrative_state", "narrative_review", "resolves_item_id", "不得按标题猜测关闭"]
  - name: anonymous-role-boundary
    required_text: ["未具名岗位", "不得创建角色卡", "合并为同一个既有角色"]
  - name: state-field-ownership
    required_text: ["通话另一端", "省略 current_location", "appearance_before", "appearance_evidence", "age_before", "age_evidence", "items_or_assets 是整字段替换", "items_or_assets_before", "逐字包含", "同场另一人物"]
  - name: relationship-and-link-identity
    required_text: ["同一有向角色对", "一个当前 relationship_type", "每个角色只出现一次", "一个 appearance_type"]
  - name: worldbuilding-anti-fragmentation
    required_text: ["独立生命周期", "操作视角", "阶段汇总", "仅声称“层级不同”不是有效的新建理由"]
  - name: stable-background-preservation
    required_text: ["background_before", "逐字包含该完整旧值", "禁止改写、缩短或删除旧背景"]
---
依本章事实和已有档案生成可写库的候选 JSONL。

【输出合同】
- 只输出 JSONL；每行一个完整 JSON 对象，不要 Markdown、解释、代码块或数组。
- 首次生成回合的首个响应对象必须同时包含两个必填对象：
  `{{"chapter_summary":{{"summary_text":"...","coverage_manifest":{{"scene_count":1,"characters":[],"worldbuilding":[],"relationships":[],"character_profiles":[]}},"narrative_state":{{"events":[],"timeline_events":[],"foreshadowing_planted":[],"foreshadowing_resolved":[],"storyline_progress":[],"new_storylines":[],"reader_known_facts":[],"character_known_facts":[],"unresolved_actions":[]}},"narrative_review":{{"source":"provided","outcome":"assessed"}}}},"chapter_outline":{{"title":"当前章节原题","summary":"...","node_type":"chapter","status":"completed"}}}}`
  系统会拆成摘要和章级大纲；不能只返回摘要后结束，不能只返回其中一个。增量修复回合不重复骨架。
- 其余候选每张独占一行并带标准 type，不得把 character_state_update、worldbuilding 或 chapter_link 打包进总对象；本回合不输出事实阶段的 chapter_overview、character_fact。
- 没有角色、设定、关系或档案变化时，清单写 []；空数组是合法结果，不得为填格式造卡。chapter_summary 的 summary_text 非空，narrative_state 没有发现时各数组也写 []，narrative_review 也须显式提供。
- coverage_manifest 是验收合同：scene_count 为独立场景数；characters 只列影响连续性的稳定角色；worldbuilding 列新增、变化或关键引用的稳定标题；relationships 列 source_name、target_name、relationship_type；character_profiles 只列新建或稳定档案新增信息的角色。清单必须与候选逐项一致。
- 多场景章除 chapter_outline 外输出 2-6 条 node_type="section" 的 outline_create，以 parent_title 绑定章节点。模型不得生成或猜测 UUID；更新必须复制上下文中的真实 id，新建不填 id。

【角色状态与档案】
- 同一身份始终使用角色卡稳定主名，别名只进 aliases；禁止组合展示名或在主名、昵称、称谓间切换。
- 每个出场稳定角色输出 character_state_update。未变化或未交代的字段必须省略，司命保留原值；没有变化时可逐字沿用一个已知状态。可更新 life_status、current_location、realm_or_level、physical_state、mental_state、current_goal、active_conflict、abilities_state 等。
- appearance、age 仅在正文确认变化时提交；修改旧值必须分别携带 appearance_before、appearance_evidence、age_before、age_evidence，before 逐字复制旧值，evidence 是本章逐字证据。电话或消息参与不证明人物身处通话另一端；未明确地点就省略 current_location。
- items_or_assets 是整字段替换。更新已有非空值时带 items_or_assets_before，并在新值中逐字包含旧值后再追加变化；空串不清除。物品须有该人物持有、控制或经手的证据，同场另一人物的物品不得错记。
- 只有不存在现存卡片的稳定身份用 character_create。character_update 必须带真实 id，只提交有依据的变化；personality、background、custom_system_prompt 一旦提交就是完整替换值。修改 background 时，background_before 须逐字复制当前完整背景，新 background 须逐字包含该完整旧值后仅追加稳定事实；自动建档禁止改写、缩短或删除旧背景。profile 只更新有证据的 core_motivation、inner_lack、core_belief、public_persona、hidden_persona、reveal_chapter、moral_taboo、voice、action_habit、trauma_trigger；日常行动进状态或时间线。
- “神秘人影”等身份未确认描述只进摘要、场景和 chapter_link，不建空白永久卡。未具名岗位、临时称谓和泛指人物不得创建角色卡、状态卡、角色关系、角色档案或角色章节关联，也不列入 coverage_manifest.characters/character_profiles；不得因为两个章节都出现相同岗位称谓就合并为同一个既有角色，除非正文与真实 id 明确证明同一人。
- role_type 只能是 protagonist、supporting、antagonist、mentor、other；身份说明写 background，不能拼入枚举。

【世界观、关系与章节关联】
- 新设定用 worldbuilding_create；已有设定变化用带真实 active id 的 worldbuilding_update，确认、使用、受损或受限可写同 id 的 worldbuilding_timeline。维度仅用 geography、history、factions、power_system、races、culture；仅关键引用且未变化时只进 chapter_link。
- 作品已有世界观时，create 必须带 identity_resolution：reviewed_existing_ids 逐字覆盖 worldbuilding_identity_review_required 的全部 ID；该列表为空时至少审阅标题索引中最接近的真实 ID。reason 逐项说明为何不是更新；canonical_title_hint 与选定卡标题不同时用 source_fact_titles 声明归入。应用只校验 ID、归属、active 状态和审阅覆盖，不替模型做语义决定。
- worldbuilding_create 只用于有独立身份、独立生命周期或状态、以后可单独变化的实体。既有流程的一步、操作视角、字段或细化规则应更新原卡；多卡集合、证据链、章节结论或阶段汇总进入摘要、账本、关联或时间线。能追加到旧卡就必须更新；仅声称“层级不同”不是有效的新建理由。reason 说明合并到逐项审阅的旧卡为何会损害其真实身份。
- 世界观清单、候选和 chapter_link 使用同一稳定标题，说明性后缀进内容。失活历史不是当前设定或更新目标。
- 正文明确且影响连续性的关系同时进入 coverage_manifest.relationships 和 character_relationship，双方须有稳定角色卡。同一有向角色对只能保留一个当前 relationship_type，不得用近义类型重复建边；地点、组织、事件不能当关系端点。
- 全章使用一条聚合 chapter_link 记录角色、设定、大纲、地点、物品、事件、重要性与顺序。characters 中每个角色只出现一次，由模型选择一个 appearance_type：当前行动或电话、消息直接参与为“出场”，只被谈及或列名为“提及”，只在回忆中出现为“回忆”；应用不猜测。
- coverage_manifest.characters、worldbuilding、character_profiles、relationships 中的每个身份分别须有同身份状态/设定或关联/档案/关系候选；重复卡不能凑数，缺项则不通过。

【候选缺项自动修复】
- 用户消息含“上一轮校验未通过”时，本节优先：系统会保留上一轮已通过候选；只补错误明确列出的缺失身份，或重发解析失败、身份不一致、结构错误的候选，不要重发完整候选集。
- 若别名、近义词或说明性标题被误列成多个实体，单独输出 type="chapter_summary"、coverage_manifest_mode="replace" 及五个完整清单；不覆盖已存摘要和账本。既有设定须解析到一个精确 id/title，不能因事实标签不同新建重复卡。
- 聚合关联有旧错误时单独提交 chapter_link_mode="replace" 及 characters、worldbuilding_titles、locations、items、events 完整数组；仅缺项则普通增补。
- chapter_summary 或 chapter_outline 只在错误明确说缺失时补。错误指出缺少状态、设定、档案、关系或章节关联时逐项补齐；结束前核对清单、名称、数量，不删除或重写已有正确卡。

【section 与叙事账本】
- 每个独立场景一条 section，含 scene_number、purpose、location、timeline、pov_character、characters、entry_state、exit_state、emotional_residue、unresolved_actions；空项写空数组或“未发生变化”。
- 所有叙事变化写入唯一 chapter_summary.narrative_state：事件、线索、伏笔、故事线和未完成行动不得另造顶层 type。条目记录稳定身份、状态、首次/最近章节、证据、置信度；foreshadowing_planted、storyline_progress、unresolved_actions 的 evidence 必须是本章可检索的 6-120 字原文，找不到就不生成。
- 解决治理项必须引用已有 resolves_item_id 或 resolves_dedupe_key；找不到稳定引用就待复核，不得按标题猜测关闭。

【判断边界】
- 只保留影响后续连续性的事实，不复述动作流水账；中文小说必须用中文建档，不要改成英文或拼音；不确定内容标注，不把推测写成事实。
- 持久化背景、设定和时间线注明实际章节或时间，不累积脱离来源的“本章、今天、明天”；未知年龄保持未知。
