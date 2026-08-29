# 正文写作与新章规划统一上下文更新计划

## 文档信息

| 项目 | 内容 |
|---|---|
| 状态 | 已集成 v3.3.6，v3.3.7 发布候选等待远程门禁 |
| 基线版本 | v3.3.5 |
| 建议目标版本 | v3.3.7 |
| 实施分支 | `feat/model-driven-chapter-context` |
| 适用入口 | PC、内部 API、直接 MCP、本机 CLI、Android |
| 约束来源 | 仓库根目录 `AGENTS.md` |

> v3.3.5 已发布并用于立项上下文治理。本计划不得改写 v3.3.5 标签、发布说明或既有 Release；完成后应作为后续版本发布。

> v3.3.6 已用于前端编辑器状态竞态修复。本计划先在独立分支完成代码与测试，再合入该版本；当前已完成合并，并重点复核了编辑器、AI 草稿状态和刷新逻辑的交叉影响。

## 一、背景与现状

v3.3.5 已将对话式立项从“全阶段、全角色展开”改为索引、围栏检索和显式引用，但该发布基线中的正文写作与新章规划尚未使用同一条上下文路径。

实施前的工作分支已经把正文写作改为：

```text
prepare_task_context
→ search_task_context（模型主动检索）
→ submit_context_evidence（模型复核、服务端精确读取）
→ context_selection_token
→ chapter_writer
→ 未保存正文草稿
```

而新章规划当时仍然是：

```text
外层 Agent 查询部分资料
→ executor 隐式创建 planning manifest
→ outline_writer 自动拼装大纲、角色、世界观和其他资料
→ create_outline_nodes
→ 正式大纲节点
```

这会产生以下问题：

1. 正文与规划形成两套上下文状态机，不符合 `AGENTS.md` 的单一业务路径要求。
2. `outline_writer` 会再次自动读取全量大纲标题、最近角色、世界观和 planning manifest，容易与外层 Agent 已读内容重复。
3. 同人文、群像作品的角色和大纲越多，规划上下文越容易膨胀。
4. 用户要求“写下一章”但不存在下一章大纲时，模型可能自动补纲、询问作者或直接失败，行为不确定。
5. 当前没有未保存大纲提案的持久化模型和界面；若禁止直接落库，作者无处查看和确认生成结果。

## 二、更新目标

1. 正文写作和新章规划共用一套模型选材、精确读取、预算和令牌状态机。
2. 模型负责理解用户最新消息、查询真实数据、选择目标 ID 和资料来源；应用代码不使用关键词、正则或界面状态替模型做语义决策。
3. 服务端只自动提供不可缺少的硬锚点，不再按项目规模自动带入角色、前文、世界观或完整大纲。
4. 规划结果先成为作者可见、可编辑、可恢复的未保存大纲提案，未经作者确认不得写入正式大纲。
5. 找不到下一章大纲时，模型可以主动生成提案，但不得在同一回合静默保存大纲并继续写正文。
6. 32k token 只作为精简软目标；实际输入容量由模型窗口动态决定。
7. PC、API、MCP、CLI 与 Android 使用相同业务契约和关键状态语义。

## 三、非目标

本次更新不包含：

- 自动执行章节建档、角色变化提取或世界观回写。
- 将基础写作、去除 AI 味和质量评审合并为一次任务。
- 正文草稿完成后自动续写下一章。
- 使用关键词表、正则或固定短语判断“是否需要补纲”。
- 保留旧自动拼装路径作为兼容或备用实现。

## 四、目标架构

### 4.1 一套状态机，两个任务契约

将正文专用的 `WritingContextSelector` 泛化为 `TaskContextSelector`，并由任务契约决定硬锚点、可搜索来源和最终生成器。

| 契约 | 硬锚点 | 最终生成器 | 作者可见结果 |
|---|---|---|---|
| `chapter_writing` | 真实章级大纲、精简立项/文风、作者要求、显式固定项 | `chapter_writer` | `ChapterDraft` |
| `outline_planning` | 父节点、插入位置、精简立项/文风、作者要求、显式固定项 | `outline_writer` | `OutlineDraft` |

两类任务统一执行：

```text
模型解析最新任务并查询真实 ID
→ prepare_task_context
→ search_task_context（零次或多次）
→ submit_context_evidence
→ 下一模型步骤携带 context_selection_token
→ 任务生成器
→ 持久化未保存草稿
→ 当前回合结束
```

### 4.2 精简基线

正文和规划的默认基线只允许包含：

- 当前作品 ID 与任务类型。
- 精简立项摘要：revision、作者约束、已选创意方向、世界与文风、artifact 状态。
- 当前作者要求。
- 正文的真实章级大纲，或规划的真实父节点与插入位置。
- 作者显式固定的来源。

默认基线不得包含：

- 全部大纲树或全部节点摘要。
- 最近章节正文或摘要。
- 角色列表、角色档案、关系和时间线。
- 世界观条目。
- 叙事治理账本。
- 自动混合检索结果和记忆。
- 未选中的立项创意候选和其他完整 artifact 正文。

`generate_creation_artifact` 仍只属于立项流程，正式作品的写章与补纲不得调用它猜测剧情。

### 4.3 模型主动检索与精确读取

`search_task_context` 允许模型按需多次检索以下来源：

- 大纲节点。
- 章节与章节摘要。
- 角色档案、关系和角色时间线。
- 世界观。
- 叙事治理数据。
- 项目记忆。

候选结果只返回：

- 真实 item/source/chunk ID。
- 来源类型与项目归属。
- 来源哈希。
- 标题和必要元数据。
- 最多约 600 字符的候选摘要。

候选摘要只用于模型复核，不得静默进入生成上下文。`submit_context_evidence` 收到模型选择后，服务端必须重新读取精确来源、验证归属和哈希、去重并生成最终 `task_context`。

模型确认硬锚点已足够时，可以提交空数组；服务端仍需签发一次性选择令牌，证明模型完成了显式复核。

### 4.4 令牌与模型步骤边界

`context_selection_token` 至少绑定：

- project ID。
- 任务类型。
- context manifest ID 与策略版本。
- 目标章级大纲，或父节点和插入位置。
- 作者要求哈希。
- 已选来源 ID、哈希及选择摘要。

必须满足：

1. `submit_context_evidence` 返回令牌后结束当前模型步骤。
2. 只有下一模型步骤可以把令牌交给 `chapter_writer` 或 `outline_writer`。
3. 跨作品、跨任务、跨目标、过期、已消费或来源已变化的令牌全部拒绝。
4. 两个生成器都禁止在缺少 manifest 或有效令牌时隐式创建默认上下文。

## 五、权威业务流程

### 5.1 已存在下一章大纲

```text
用户要求写下一章
→ 模型查询真实大纲并取得章级节点 ID
→ 建立正文精简基线
→ 模型检索、复核并提交资料
→ chapter_writer 生成 ChapterDraft
→ 正文编辑器显示未保存草稿
→ 本轮结束
```

作者随后自行选择“保存并建档”或“仅保存”。正文草稿生成成功后不得自动评审、改写、建档或续写。

### 5.2 不存在下一章大纲

```text
用户要求写下一章
→ 模型查询后确认不存在可用章级节点
→ 返回/记录结构化 missing_chapter_outline 状态
→ 模型进入 outline_planning
→ 建立规划精简基线
→ 模型检索、复核并提交资料
→ outline_writer 生成 OutlineDraft
→ 大纲提案对作者可见
→ 本轮结束
```

此时必须满足：

- 不调用 `create_outline_nodes` 写入正式大纲。
- 不继续调用 `chapter_writer`。
- 不因用户界面当前选中节点而绑定目标。
- 不在后台等待作者或自动开启新回合。

作者确认大纲提案后，系统写入正式大纲并返回真实章级节点 ID；“确认大纲并写正文”按钮随后以作者本次点击为授权启动一个新的正文任务。

### 5.3 用户只要求规划大纲

模型完成相同的 `outline_planning` 选材流程并生成 `OutlineDraft`。作者可以编辑、保存、放弃或重新生成；没有明确写章请求时不得自动开始正文任务。

## 六、未保存大纲提案

### 6.1 数据模型

新增持久化 `OutlineDraft`，建议字段如下：

| 字段 | 用途 |
|---|---|
| `id` | 草稿 ID |
| `project_id` | 作品归属 |
| `status` | `pending/confirmed/discarded/superseded` |
| `parent_id` | 正式父节点 ID，可为空 |
| `insert_after_id` | 拟插入位置，可为空 |
| `nodes_json` | chapter 与 section 草稿节点 |
| `design_notes` | 模型设计说明 |
| `context_manifest_id` | 生成依据 |
| `context_selection_digest` | 精确选材摘要 |
| `base_outline_hash` | 生成时正式大纲指纹，用于并发校验 |
| `saved_outline_node_ids_json` | 确认后真实节点 ID 列表 |
| `created_at/updated_at/confirmed_at` | 生命周期时间 |

每个草稿节点使用本批次唯一标题作为临时键，并通过 `parent_title` 表达 chapter/section 层级；服务端强制标题唯一、父引用存在、类型合法且无环。正式写入前不得伪造数据库 ID。

同一作品默认只允许一个 pending 大纲提案。检测到现有 pending 草稿时返回其 ID，不得静默覆盖；作者明确重新生成后，旧草稿标记为 `superseded`。

### 6.2 服务端接口与工具

新增或调整：

- `GET /projects/{project_id}/outline-drafts/pending`
- `PUT /projects/{project_id}/outline-drafts/{draft_id}`
- `POST /projects/{project_id}/outline-drafts/{draft_id}/confirm`
- `POST /projects/{project_id}/outline-drafts/{draft_id}/regenerate`
- `DELETE /projects/{project_id}/outline-drafts/{draft_id}`
- API-free Agent 使用的 `save_external_outline_draft`

内部 `outline_writer` 和外部 Agent 生成的大纲都必须进入同一 `OutlineDraft` 服务。`confirm_outline_draft` 必须：

1. 锁定当前作品的大纲写入。
2. 比较 `base_outline_hash`，检测生成后发生的正式大纲变化。
3. 验证父节点归属、节点类型、临时父子关系和最多允许的本批次节点结构。
4. 在同一事务中创建 chapter 与 section，并保存临时 ID 到真实 ID 的映射。
5. 将草稿标记为 confirmed；部分成功必须回滚，不能形成半批节点。

`confirm_outline_draft` 只负责确认大纲。“确认大纲并写正文”由前端在确认成功并取得真实章级节点 ID 后，启动新的 Agent 请求，不在确认接口内部偷偷调用模型。

### 6.3 PC 交互

`outline_writer` 完成后通过权威 `complete.applied_actions` 终态载荷返回大纲草稿动作，聊天区域显示紧凑“大纲提案卡片”：

- 章标题。
- 2–6 个 section 标题。
- 简短摘要。
- “查看并编辑”入口。

完整节点不得作为长 JSON 堆入聊天记录。

在现有 `OutlinePage` 中：

- 把 pending 草稿节点以虚线或专用颜色叠加到拟插入位置。
- 节点明确标记“AI 提案 · 未保存”，不得混入正式接口返回的节点集合。
- 选择草稿节点后复用大纲表单，允许编辑标题、摘要、角色和 section 数据。
- 提供“仅保存大纲”“确认大纲并写正文”“重新生成”“放弃提案”操作。
- 刷新页面或重启应用后通过 pending 草稿接口恢复提案。

### 6.4 Android 交互

Android 使用同一接口和状态字段，以全屏提案页或底部全屏 Sheet 展示 chapter/section 树和编辑内容。按钮语义、冲突提示、恢复行为及最终状态必须与 PC 一致，不依赖 PC 客户端在线。

## 七、上下文预算

1. 32k token 是精简软目标，超过时记录警告但允许继续。
2. 实际输入容量为：模型上下文窗口减去系统提示、工具定义、输出预留和安全余量。
3. 删除固定 10k 输入上限。
4. 删除 `outline_writer` 固定 4000 输出上限，改用模型输出能力与 manifest 输出预留的较小值。
5. 最终选材不使用固定单条字符截断或固定 24 条来源作为业务硬上限，只按真实 token 容量判断。
6. 模型明确选中的来源由服务端精确读取完整内容；候选阶段的短摘要不能替代最终来源。
7. 单次搜索仍限制最多约 20 个短候选，以控制 tool result；模型可以分页和多轮检索。
8. 防滥用使用请求体大小、速率限制和实际模型容量，不使用与项目语义无关的角色数或来源数硬截断。

## 八、代码改造清单

### 8.1 上下文服务

- 将 `writing_context_selection.py` 重构为通用 `task_context_selection.py`。
- 将 `WritingContextSelector` 改为 `TaskContextSelector`。
- 将正文专用精确来源解析器泛化为任务来源解析器。
- 在任务契约中定义 baseline categories、search source types、generator 与 token target binding。
- 让 `writing` 和 `outline_planning` 都在 baseline 后立即停止自动 current-state、hybrid retrieval 和 memory 注入。
- `submit_context_evidence` 统一返回 `task_context`、token、预算、软目标和警告。
- 将 context policy version 提升到 v4；旧 manifest 必须标记 stale 并要求重新准备。

### 8.2 生成器与执行器

- `chapter_writer` 改用通用选择验证器，保留未保存正文草稿终止边界。
- 删除 `outline_writer` 内部的全量大纲、最近 30 个角色、`_build_world_context` 和重复 style 拼装。
- `outline_writer` 只使用已验证的 `task_context` 和规划硬锚点。
- `outline_writer` 成功后持久化 `OutlineDraft` 并返回草稿 ID，不返回正式节点成功语义。
- `executor` 不再为 `chapter_writer` 或 `outline_writer` 隐式创建 manifest。
- 缺少目标、manifest 或 token 时返回结构化 `needs_confirmation` 与明确 next tool，不伪装成功。
- 新增大纲草稿的服务、路由、事务锁、并发指纹校验和恢复逻辑。

### 8.3 Agent、工具与提示词

- 更新 PC workspace PromptSpec、MCP prompt、CLI worker 和移动端 prompt contract。
- 明确“查不到下一章大纲”只能生成提案并结束本轮。
- 明确 `outline_batch_count` 只在模型已决定规划时生效，不能触发规划或连续正文。
- 工具描述统一使用 `task_context` 和通用 token 语义。
- 保持首步只开放 `set_tool_categories`；类别切换后当前模型步骤立即结束。
- 由模型选择类别、查询和目标 ID，应用层只校验类别、权限、归属、类型和状态。

### 8.4 前端与移动端

- 将 `AiPanelContext` 的正文专用草稿状态扩展为可区分 `chapter` 与 `outline` 的任务草稿联合类型，或新增独立 `generatedOutlineDraft` 状态。
- 在既有 SSE `complete.applied_actions` 终态中识别大纲草稿，并增加聊天提案卡片；不另建第二套事件状态。
- 在 `OutlinePage` 增加 pending 草稿恢复、虚拟树节点、编辑和确认操作。
- “确认大纲并写正文”必须在确认成功后发起一个新的作者授权请求。
- 同步 Android DTO、Room/缓存策略（如需）、Repository、ViewModel 与界面状态。
- 更新 OpenAPI 生成类型、PC/Android 契约快照和移动端一致性文档。

### 8.5 删除旧路径

完成迁移后删除：

- `WritingContextSelector` 及正文专用返回字段。
- planning 自动拼装上下文分支。
- `outline_writer` 的数据库自动取材逻辑。
- 把 `create_outline_nodes` 当作 `outline_writer` 固定下一步的提示词和测试。
- 描述旧自动规划、旧固定预算或直接保存大纲的文档与契约。

不得保留静默 fallback、双状态机或“新路径失败后回到自动拼装”的兼容分支。

## 九、数据库与迁移

- 新增 `outline_drafts` 表及必要索引。
- 迁移只增加可空/独立数据，不修改 v3.3.5 的正式大纲和章节数据。
- 旧版本不存在 OutlineDraft，因此无需导入旧草稿。
- 升级后第一次运行应安全创建表；降级应用可以忽略新增表，但不得在新版本运行时维护两套业务路径。
- 迁移测试必须覆盖空库、v3.3.5 数据库升级和已有大纲/章节的大型项目。

## 十、测试计划

### 10.1 后端单元与集成测试

- 正文和规划共用同一 selector、source resolver、token 校验与预算实现。
- 两类 baseline 都不自动包含角色、前文、世界观、治理数据或检索结果。
- 规划 baseline 不包含完整大纲树和未选立项候选。
- 多轮搜索结果只返回短摘要，未提交候选不得进入 `task_context`。
- 提交空数组可以得到有效令牌。
- 超过 32k 只告警；超过真实模型容量才拒绝。
- 不再存在固定 24 条最终来源限制。
- 令牌跨作品、跨任务、跨目标、重放和来源变化全部失败。
- 缺少下一章大纲时不产生正式 OutlineNode 或 ChapterDraft。
- `OutlineDraft` 在服务重启后可恢复。
- 确认草稿事务性创建 chapter/section；任一节点失败时全部回滚。
- 大纲指纹变化时确认失败并保留草稿。
- 同一作品并发生成不会产生两个 pending 草稿或重复正式节点。

### 10.2 Agent 行为测试

- 用户明确要求规划时，模型自行开放类别并检索资料。
- 用户只讨论剧情时，不写入正式大纲。
- 用户要求写下一章且有大纲时，直接进入正文选材。
- 用户要求写下一章且无大纲时，只生成 OutlineDraft 并结束。
- 历史消息、界面选中项和 `outline_batch_count` 不会覆盖最新用户意图。
- `submit_context_evidence` 与生成器不能处于同一模型步骤。
- 正文草稿或大纲提案生成成功后，服务端确定性结束本轮。

### 10.3 前端与移动端测试

- 聊天卡片能打开对应提案。
- 大纲树正确显示虚拟节点且不污染正式树。
- 编辑草稿不会提前更新正式大纲。
- 刷新、重启、切换页面后可以恢复 pending 提案。
- 保存、保存并写正文、重新生成和放弃的状态一致。
- 大纲冲突提示不会丢失作者修改。
- Android 离开 PC 后可独立完成查看、编辑、确认和放弃。

### 10.4 大项目回归

至少构造：

- 300 个以上角色及关系。
- 1000 个以上大纲/section 节点。
- 200 个以上章节。
- 200 条以上世界观。

验收正文和规划的初始 baseline 大小不随这些集合线性增长；只有模型明确选择的资料进入最终上下文。

## 十一、实施顺序

### 阶段 A：统一上下文内核

1. 泛化 selector 和 source resolver。
2. 增加 `outline_planning` 契约。
3. 统一 `task_context` 和 token。
4. 移除正文与规划的自动拼装分叉。
5. 完成上下文、预算和令牌测试。

### 阶段 B：大纲草稿后端

1. 增加数据库迁移和模型。
2. 实现 OutlineDraft 服务和 API。
3. 改造 `outline_writer` 与 API-free 保存入口。
4. 实现确认事务、冲突和终止边界。

### 阶段 C：PC 作者确认界面

1. 接入 SSE 大纲草稿事件。
2. 增加聊天提案卡。
3. 改造 OutlinePage 草稿预览和编辑。
4. 实现确认、继续写作、重新生成和放弃。

### 阶段 D：MCP、CLI 与 Android 对齐

1. 更新外部 Agent 工作流和工具契约。
2. 导出移动端 PC 权威契约。
3. 实现 Android 独立提案界面与恢复。
4. 运行跨端一致性测试。

### 阶段 E：删除旧路径并发布

1. 删除废弃代码、字段、提示词、测试和文档。
2. 更新架构基线、移动端一致性表和下一版本发布说明。
3. 运行完整发布门禁。
4. 合并 PR、创建新版本标签和 Release。
5. 确认远端临时分支删除，并关闭本地临时分支。

## 十二、验收标准

全部满足后方可发布：

- [x] 正文和新章规划只有一套上下文选择状态机。
- [x] `outline_writer` 不再自动读取项目故事数据。
- [x] 正文和规划都只能使用模型复核后由服务端精确读取的来源。
- [x] 不存在固定 10k 输入限制或固定 24 条最终来源限制。
- [x] 32k 仅为软目标，真实模型容量是唯一输入硬边界。
- [x] 找不到下一章大纲时只生成未保存提案。
- [x] 作者能在 PC 与 Android 查看、编辑、确认、重生成和放弃提案。
- [x] 未确认提案不会写入正式大纲，也不会触发正文生成。
- [x] 确认大纲后使用返回的真实章级 ID 启动新的正文任务。
- [x] 正文草稿与大纲提案成功后都确定性结束当前模型回合。
- [x] API、MCP、CLI、PC 和 Android 的状态及错误语义一致。
- [ ] 架构检查、后端测试、前端检查、Android 测试和发布门禁全部通过。
- [x] 旧自动拼装与双路径实现、测试和文档已删除。

当前验证记录：合入 v3.3.6 后，后端架构、PromptSpec/预算和导入边界均通过；清除测试环境代理并排除 3 个当前容器会提前终止短生命周期子进程的环境限定用例后，完整后端为 1800 passed、3 deselected。前端 lint、全量 221 项测试、架构、重复度、OpenAPI 和生产构建均通过；移动端/PC 的 27 项能力契约和 Python 侧移动契约测试均通过；发布打包/更新契约 44 项测试和性能预算基线也已通过。Android Gradle 测试仍因当前环境无法下载 Gradle 8.9 而未执行，交由联网的 GitHub Actions 发布门禁完成。

## 十三、发布与回滚

建议发布为 v3.3.7：

1. 不修改或重打 v3.3.5 标签。
2. 更新 PC、Android、安装包、容器镜像和文档中的版本号。
3. 运行仓库规定的架构检查、后端测试、前端 lint/build、Android 测试和 release gate。
4. 合并后从主分支创建 v3.3.7 标签与 Release，核对 Windows 和 Android 产物及 SHA-256。
5. 删除远端临时分支并切回主分支；本地分支仅在确认提交已合并且可恢复后关闭。

若发布后出现阻断问题，应回滚新版本提交并发布修复版本；不得在运行时重新开启旧自动拼装路径作为隐藏 fallback。新增的 `outline_drafts` 表可以保留，旧应用会忽略它，待修复版本继续读取。
