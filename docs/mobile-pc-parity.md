# Android ↔ PC 能力对齐契约

> 本文由 `contracts/mobile-pc-parity.json` 通过 `scripts/check-mobile-pc-parity.py` 生成；请勿手工修改。

PC 是小说数据、领域副作用和上下文治理的唯一权威实现。Android 在线模式应尽量作为薄客户端；离线模式只允许可验证回放的修订；手机独立 Agent 的降级能力必须显式记录。

当前共登记 **27** 项能力：**10** 项已对齐、**17** 项部分对齐、**0** 项待实现。

## 总览

| 能力 | 权威入口 | Android 在线 | Android 离线 | Android 独立 Agent | 状态 |
|---|---|---|---|---|---|
| `assistant.workspace` | workspace assistant stream / MobileWorkspaceAgent | 调用 PC 权威接口 | 明确阻止 | 明确降级实现 | 部分对齐 |
| `authoring.chapter` | /api/v1/projects/{project_id}/chapters | 调用 PC 权威接口 | 修订队列回放 | 修订队列回放 | 已对齐 |
| `authoring.character` | /api/v1/projects/{project_id}/characters | 调用 PC 权威接口 | 修订队列回放 | 修订队列回放 | 已对齐 |
| `authoring.document_import` | /api/v1/import/project-file | 调用 PC 权威接口 | 本地副本 | 本地副本 | 部分对齐 |
| `authoring.exchange_package` | /api/v1/projects/project-package/import | 调用 PC 权威接口 | 本地副本 | 本地副本 | 已对齐 |
| `authoring.export` | /api/v1/projects/{project_id}/export | 调用 PC 权威接口 | 明确降级实现 | 明确降级实现 | 部分对齐 |
| `authoring.outline` | /api/v1/projects/{project_id}/outline | 调用 PC 权威接口 | 修订队列回放 | 修订队列回放 | 已对齐 |
| `authoring.project` | /api/v1/projects/{project_id} | 调用 PC 权威接口 | 修订队列回放 | 修订队列回放 | 已对齐 |
| `authoring.worldbuilding` | /api/v1/projects/{project_id}/worldbuilding | 调用 PC 权威接口 | 修订队列回放 | 修订队列回放 | 已对齐 |
| `chapter.cataloging` | /api/v1/projects/{project_id}/cataloging | 调用 PC 权威接口 | 明确阻止 | 明确阻止 | 部分对齐 |
| `chapter.history` | GET /chapters/{chapter_id}/snapshots[/diff/{snapshot_id}] | 调用 PC 只读接口 | 明确阻止 | 尚未支持 | 部分对齐 |
| `chapter.reorder` | PUT /api/v1/projects/{project_id}/chapters/reorder | 调用 PC 权威接口 | 明确阻止 | 明确阻止 | 已对齐 |
| `chapter.restore` | POST /chapters/{chapter_id}/restore/{snapshot_id} | 调用 PC 权威接口 | 明确阻止 | 明确阻止 | 已对齐 |
| `character.ai_config` | GET/PUT /characters/{character_id}/ai-config | 调用 PC 权威接口 | 修订队列回放 | 尚未支持 | 部分对齐 |
| `character.relationships` | GET /characters/relationships; PUT /characters/{character_id}/relationships | 调用 PC 权威接口 | 修订队列回放 | 明确降级实现 | 部分对齐 |
| `character.versions` | GET /characters/{character_id}/versions[/{version_id}] | 调用 PC 只读接口 | 明确阻止 | 尚未支持 | 部分对齐 |
| `context.selection` | prepare_task_context -> search_task_context -> submit_context_evidence | 调用 PC 权威接口 | 明确阻止 | 明确降级实现 | 部分对齐 |
| `governance.items` | /narrative-governance/items[/{type}/{id}] | 调用 PC 权威接口 | 修订队列回放 | 明确降级实现 | 部分对齐 |
| `novel_creation.session` | /api/v1/novel-creation/* | 调用 PC 权威接口 | 本地副本 | 明确降级实现 | 部分对齐 |
| `sync.conflicts` | POST /api/v1/sync/conflicts/{conflict_id}/resolve | 调用 PC 权威接口 | 明确阻止 | 不适用 | 已对齐 |
| `sync.replication` | /api/v1/sync/{bootstrap,push,pull} | 调用 PC 权威接口 | 本地副本 | 本地副本 | 已对齐 |
| `worldbuilding.history` | GET /worldbuilding/{entry_id}/{versions\|timeline} | 调用 PC 只读接口 | 明确阻止 | 尚未支持 | 部分对齐 |
| `worldbuilding.relationships` | WorldbuildingRelation sync record | 修订队列回放 | 修订队列回放 | 尚未支持 | 部分对齐 |
| `writer.chapter` | chapter_writer | 调用 PC 权威接口 | 明确阻止 | 明确降级实现 | 部分对齐 |
| `writer.character` | character_writer | 调用 PC 权威接口 | 明确阻止 | 明确降级实现 | 部分对齐 |
| `writer.outline` | outline_writer | 调用 PC 权威接口 | 明确阻止 | 明确降级实现 | 部分对齐 |
| `writer.worldbuilding` | worldbuilding_writer | 调用 PC 权威接口 | 明确阻止 | 明确降级实现 | 部分对齐 |

## 详细能力

### `assistant.workspace` — 统一的工作区 Agent 对话与工具循环

- **权威入口：** `workspace assistant stream / MobileWorkspaceAgent`（`pc_workspace_tool`）
- **状态：** 部分对齐
- **副作用：** 无写入副作用
- **幂等策略：** `not_applicable`；只读或无需防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 明确阻止：没有 Gateway 且未配置手机直连模型时不启动 Agent。
- **Android 独立 Agent：** 明确降级实现：提示词、工具 schema、宽粒度类别控制器与版本化上下文策略均由 PC 源生成；模型正文、推理和函数参数使用供应商 SSE 真流式解析，thinking 模型沿用 PC 的 provider-safe tool_choice 规则并在接口明确拒绝时于输出前自动去参重试，章节草稿、完整 ContextManifest、通用会话转录与工具日志均可跨重启恢复。独立运行仍不进入 PC 的数据库级 AgentRun 审计，检索仍是确定性词法降级。
- **已知缺口：**
  - 手机独立 Agent 的章节草稿、通用对话和工具日志已支持跨重启恢复，但独立运行不会伪装成 PC 数据库中的 AgentRun 审计记录。

### `authoring.chapter` — 章节创建、读取、更新和删除

- **权威入口：** `/api/v1/projects/{project_id}/chapters`（`pc_http`）
- **状态：** 已对齐
- **副作用：** chapter_snapshot、narrative_checkpoint、cataloging、content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 修订队列回放
- **Android 独立 Agent：** 修订队列回放：草稿和 ContextManifest 先写入本机运行日志，写作模型随后立即停止；正式章节保存及是否启动建档由作者在界面选择。

### `authoring.character` — 角色卡创建、读取、更新和删除

- **权威入口：** `/api/v1/projects/{project_id}/characters`（`pc_http`）
- **状态：** 已对齐
- **副作用：** character_version、alias_sync、content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 修订队列回放
- **Android 独立 Agent：** 修订队列回放：本地写入统一投影为 PC Character 公共契约。

### `authoring.document_import` — 小说 TXT / Markdown / DOCX 批量导入与章节拆分

- **权威入口：** `/api/v1/import/project-file`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** content_sync
- **幂等策略：** `client_serialization`；必须防重
- **幂等限制：** Android 一次只提交一个导入文件；服务端当前未接收独立 request key。
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 本地副本：本机直接解析 TXT / Markdown / DOCX，文本与 PC 共用多编码回归样本且严格拒绝替换字符；Markdown 标题只参与章节边界识别，作品和章节在同一本地事务中写入后进入 outbox。
- **Android 独立 Agent：** 本地副本：手机独立模式可直接导入与 PC 相同的 TXT / Markdown / DOCX，不要求 PC 正在运行。
- **已知缺口：**
  - 离线和手机独立导入先在本地事务中建档，再经 outbox 回放，不会生成 PC 单次导入接口的服务端操作审计记录；TXT / Markdown / DOCX 输入格式和文本编码结果已对齐。

### `authoring.exchange_package` — 司命项目包完整/结构档位的流式导入导出

- **权威入口：** `/api/v1/projects/project-package/import`（`pc_http`）
- **状态：** 已对齐
- **副作用：** content_sync、deterministic_index_rebuild
- **幂等策略：** `request_key`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 本地副本：先流式落盘并完成协议校验，再恢复可编辑副本；完整原包、请求键和暂不展示的合法集合持续保留，联网后先上传项目包再回放普通 outbox。
- **Android 独立 Agent：** 本地副本：可从保留的完整原包叠加本机最新作者资料并生成相同 v1 格式；暂不展示的集合和素材不会丢失，也不会启动 Agent、建档或自动任务。

### `authoring.export` — 小说 TXT / Word / PDF 导出与本机保存

- **权威入口：** `/api/v1/projects/{project_id}/export`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** 无写入副作用
- **幂等策略：** `not_applicable`；只读或无需防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 明确降级实现：离线只导出本机章节 TXT；Word/PDF 需要 PC 的正式导出服务。
- **Android 独立 Agent：** 明确降级实现：手机独立模式可导出 TXT，Word/PDF 连接 PC 后生成。
- **已知缺口：**
  - 离线和手机独立模式只支持 TXT；Word / PDF 仍需连接 PC 权威导出服务。

### `authoring.outline` — 大纲树创建、读取、更新、删除和同级排序

- **权威入口：** `/api/v1/projects/{project_id}/outline`（`pc_http`）
- **状态：** 已对齐
- **副作用：** cycle_validation、character_link_replace、content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 修订队列回放
- **Android 独立 Agent：** 修订队列回放

### `authoring.project` — 作品创建、读取、更新和删除

- **权威入口：** `/api/v1/projects/{project_id}`（`pc_http`）
- **状态：** 已对齐
- **副作用：** content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 修订队列回放
- **Android 独立 Agent：** 修订队列回放

### `authoring.worldbuilding` — 世界观条目创建、读取、更新和删除

- **权威入口：** `/api/v1/projects/{project_id}/worldbuilding`（`pc_http`）
- **状态：** 已对齐
- **副作用：** world_version、content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 修订队列回放
- **Android 独立 Agent：** 修订队列回放

### `chapter.cataloging` — 导入或既有章节的作品建档任务

- **权威入口：** `/api/v1/projects/{project_id}/cataloging`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** cataloging
- **幂等策略：** `client_serialization`；必须防重
- **幂等限制：** Android 同一时刻只启动一个建档任务；服务端任务 ID 作为后续流式进度、查询与取消的唯一引用。
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 明确阻止：完整建档会同时更新摘要、角色、世界观和治理资料，离线时不复制第二套权威实现。
- **Android 独立 Agent：** 明确阻止：手机独立 Agent 暂不伪装成 PC Cataloging；连接 Gateway 后运行权威建档。
- **已知缺口：**
  - 手机独立模型尚未实现与 PC 完全一致的批量 Cataloging 运行时。

### `chapter.history` — 章节快照列表、详情与差异比较

- **权威入口：** `GET /chapters/{chapter_id}/snapshots[/diff/{snapshot_id}]`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** 无写入副作用
- **幂等策略：** `read_only`；只读或无需防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 只读接口
- **Android 离线：** 明确阻止：高级历史页面明确要求连接 PC Gateway，避免把不完整缓存当作权威版本。
- **Android 独立 Agent：** 尚未支持：手机独立 Agent 不维护 PC 版本账本。
- **已知缺口：**
  - 离线副本中即使存在历史记录也不会开放恢复或 diff，以免版本链不完整。

### `chapter.reorder` — 章节权威阅读顺序重排

- **权威入口：** `PUT /api/v1/projects/{project_id}/chapters/reorder`（`pc_http`）
- **状态：** 已对齐
- **副作用：** authoritative_reorder、content_sync
- **幂等策略：** `set_replacement`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 明确阻止：排序必须一次提交当前作品全部章节，离线副本不能猜测 authoritative sort_order。
- **Android 独立 Agent：** 明确阻止：独立 Agent 不直接改变 PC 权威章节顺序。

### `chapter.restore` — 从历史快照恢复章节及关联叙事状态

- **权威入口：** `POST /chapters/{chapter_id}/restore/{snapshot_id}`（`pc_http`）
- **状态：** 已对齐
- **副作用：** chapter_restore、chapter_snapshot、ledger_restore、governance_invalidation、cataloging、content_sync
- **幂等策略：** `client_serialization`；必须防重
- **幂等限制：** Android 已串行化恢复并阻止重复点击；PC 恢复接口每次调用仍会创建一个新 restore 版本，后续应增加服务端 request key。
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 明确阻止：恢复会创建新版本并恢复 ledger，必须连接 PC。
- **Android 独立 Agent：** 明确阻止：独立 Agent 不具备 PC ledger/checkpoint 权威数据。

### `character.ai_config` — 角色专用语气、口头禅与模型配置

- **权威入口：** `GET/PUT /characters/{character_id}/ai-config`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 修订队列回放
- **Android 独立 Agent：** 尚未支持：手机独立 Agent 不消费 CharacterAIConfig；普通章节写作明确避免错误注入。
- **已知缺口：**
  - CharacterAIConfig 仅在连接 PC 的高级页面和同步回放中可用，尚未接入手机独立角色扮演能力。

### `character.relationships` — 有方向的角色关系网读取与替换

- **权威入口：** `GET /characters/relationships; PUT /characters/{character_id}/relationships`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** relationship_replace、content_sync
- **幂等策略：** `set_replacement`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 修订队列回放：离线以单条有向边回放，不模拟在线“替换当前角色全部关系”的事务。
- **Android 独立 Agent：** 明确降级实现：能消费缓存关系并写入创作上下文，但没有离线专用完整关系网编辑器。
- **已知缺口：**
  - 手机在线编辑复用 PC 全量替换接口；离线回放仍是逐边 upsert，事务语义不同。

### `character.versions` — 角色版本列表与历史快照查看

- **权威入口：** `GET /characters/{character_id}/versions[/{version_id}]`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** 无写入副作用
- **幂等策略：** `read_only`；只读或无需防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 只读接口
- **Android 离线：** 明确阻止：离线列表可能缺少完整历史，界面不把缓存冒充权威版本。
- **Android 独立 Agent：** 尚未支持：独立 Agent 不维护角色版本账本。
- **已知缺口：**
  - Android 当前只读查看，尚未提供角色历史恢复。

### `context.selection` — 模型驱动的正文/大纲上下文检索与复核

- **权威入口：** `prepare_task_context -> search_task_context -> submit_context_evidence`（`pc_workspace_tool`）
- **状态：** 部分对齐
- **副作用：** 无写入副作用
- **幂等策略：** `read_only`；只读或无需防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口：通过 PC workspace assistant 调用同一工具。
- **Android 离线：** 明确阻止：没有模型执行路由时只保留资料缓存，不运行模型检索与选择流程。
- **Android 独立 Agent：** 明确降级实现：正文与大纲规划共享 PC 导出的任务策略、精简基线、模型检索、精确来源复核、动态模型容量、source hash、一次性不可猜测选择令牌和 stale 校验。模型选中的精确来源不设固定单条字符或来源数量上限，仅受当前模型可用输入容量约束。Android 独立模式以模型发起的本地词法检索替代 PC 混合检索，不支持 pinned chunks，也不写入 PC 数据库级 ContextManifest 审计。
- **已知缺口：**
  - 手机独立检索尚未复用 PC 的 FTS、语义嵌入、pinned chunks 与数据库级 ContextManifest 审计；实际写章清单已在本机运行日志中持久化。

### `governance.items` — 伏笔、叙事债务及生命周期状态操作

- **权威入口：** `/narrative-governance/items[/{type}/{id}]`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** governance_transition、content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 修订队列回放
- **Android 独立 Agent：** 明确降级实现：可读取最新治理锁并用于写章，但独立 Agent 未开放治理项创建、复检和关闭工具。
- **已知缺口：**
  - 手机独立 Agent 目前只消费治理锁，不执行治理生命周期命令。

### `novel_creation.session` — 对话式立项会话、阶段确认和正式归档

- **权威入口：** `/api/v1/novel-creation/*`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** project_archive、structured_entities、content_sync
- **幂等策略：** `request_key`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 本地副本：会话先保存在手机，正式归档后进入规范同步。
- **Android 独立 Agent：** 明确降级实现：使用与 PC 构建时导出的 V3 PromptSpec、八个完整阶段（包含创作约束）、标准化、依赖失效和最终建档门槛；结构化建档页中的创意选择、阶段生成/编辑/确认、最终审阅和正式建档体验与 PC 对齐。手机独立执行仍没有 PC 服务端 durable Operation/AgentRun 审计记录。
- **已知缺口：**
  - 手机独立模式没有 PC 服务端 durable Operation/AgentRun 的暂停、恢复与审计记录；建档数据结构、确认门槛和用户操作已通过同源契约与回归测试对齐。

### `sync.conflicts` — 同步冲突查看与用户选择解决

- **权威入口：** `POST /api/v1/sync/conflicts/{conflict_id}/resolve`（`pc_sync_protocol`）
- **状态：** 已对齐
- **副作用：** conflict_resolution、sync_cursor
- **幂等策略：** `server_transaction`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 明确阻止：冲突选择必须读取服务端当前分支。
- **Android 独立 Agent：** 不适用：未连接 Gateway 时没有远端冲突可解决。

### `sync.replication` — 修订、outbox、bootstrap、push/pull 和 tombstone 同步

- **权威入口：** `/api/v1/sync/{bootstrap,push,pull}`（`pc_sync_protocol`）
- **状态：** 已对齐
- **副作用：** revision_order、tombstone、conflict_detection、sync_cursor
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 本地副本：本地修订和 tombstone 保存在 Room/outbox。
- **Android 独立 Agent：** 本地副本：Agent 写入同一副本/outbox。

### `worldbuilding.history` — 世界观版本与时间线查看

- **权威入口：** `GET /worldbuilding/{entry_id}/{versions|timeline}`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** 无写入副作用
- **幂等策略：** `read_only`；只读或无需防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 只读接口
- **Android 离线：** 明确阻止：历史入口要求完整 PC 版本链。
- **Android 独立 Agent：** 尚未支持：手机独立 Agent 不维护世界观版本和时间线账本。
- **已知缺口：**
  - Android 当前只读查看，尚未提供世界观历史恢复。

### `worldbuilding.relationships` — 世界观条目之间的结构化关系边

- **权威入口：** `WorldbuildingRelation sync record`（`pc_sync_protocol`）
- **状态：** 部分对齐
- **副作用：** content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 修订队列回放：当前没有专用 PC HTTP 编辑路由，在线写入仍走同步契约。
- **Android 离线：** 修订队列回放
- **Android 独立 Agent：** 尚未支持：世界观写作上下文尚未消费 WorldbuildingRelation。
- **已知缺口：**
  - PC 尚无世界观关系专用 HTTP 领域命令；手机独立写作也尚未消费关系边。

### `writer.chapter` — 根据大纲和受治理上下文生成章节草稿

- **权威入口：** `chapter_writer`（`pc_workspace_tool`）
- **状态：** 部分对齐
- **副作用：** draft_store
- **幂等策略：** `draft_reference`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 明确阻止：无模型执行路由时只编辑资料，不生成正文。
- **Android 独立 Agent：** 明确降级实现：写章前创建或校验 ContextManifest，并在生成开始前一次性消费选择令牌；生成前持久化运行、成功后原子保存草稿与完整清单。同一请求使用确定性 run ID，应用重启和断流恢复到同一未保存草稿。正式保存与建档由作者操作。检索仍为 Android 本地词法降级，审计未上传到 PC 账本。
- **已知缺口：**
  - 手机独立写章已具备持久 ContextManifest、取消状态和跨重启防重；仍缺 PC 语义检索和数据库级 AgentRun 审计。

### `writer.character` — 根据作品与世界观生成结构化角色卡

- **权威入口：** `character_writer`（`pc_workspace_tool`）
- **状态：** 部分对齐
- **副作用：** draft_store
- **幂等策略：** `draft_reference`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 明确阻止：没有手机直连模型时不运行生成器。
- **Android 独立 Agent：** 明确降级实现：提示词和输出工具由 PC 构建时导出，但上下文检索和后续领域副作用在手机端执行。
- **已知缺口：**
  - 手机独立生成器尚未绑定 PC ContextManifest 和运行审计。

### `writer.outline` — 生成并由作者确认结构化大纲提案

- **权威入口：** `outline_writer`（`pc_workspace_tool`）
- **状态：** 部分对齐
- **副作用：** draft_store
- **幂等策略：** `draft_reference`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 明确阻止：没有手机直连模型时不运行生成器。
- **Android 独立 Agent：** 明确降级实现：使用 PC 导出的提示词、输出工具和 outline_planning 策略，生成开始前一次性消费选择令牌，并持久化单一 pending 提案；支持跨重启恢复、编辑、确认、确认后新回合写作、重新生成和放弃。提案超量或结构非法时整体拒绝，不静默截断；确认时按卷、章、节的依赖顺序写入。确认前不写正式大纲，并以正式大纲指纹拒绝过期提案。
- **已知缺口：**
  - 手机独立检索使用本地词法索引，且 ContextManifest/OutlineDraft 审计保存在本机而不是 PC 数据库；连接 Gateway 时使用 PC 权威实现。

### `writer.worldbuilding` — 生成结构化世界观条目

- **权威入口：** `worldbuilding_writer`（`pc_workspace_tool`）
- **状态：** 部分对齐
- **副作用：** draft_store
- **幂等策略：** `draft_reference`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 明确阻止：没有手机直连模型时不运行生成器。
- **Android 独立 Agent：** 明确降级实现：提示词和输出工具共享，但未消费世界观关系边，也没有 PC 上下文清单审计。
- **已知缺口：**
  - 手机独立世界观生成尚未消费 WorldbuildingRelation 或 ContextManifest。

## 维护规则

1. 新增 `PcApiPaths` 方法、`PcAuthoringContract` 可写类型或 `MobileWorkspaceAgent` 工具时，必须在契约中做出能力归属或写明忽略理由。
2. 声称已实现的模式必须引用实际源码；每项能力必须引用至少一个回归测试。
3. `degraded`、`unsupported` 或高风险写操作必须明确限制和幂等策略，不能以“行为大致相同”代替契约。
4. 修改契约后运行：`python scripts/check-mobile-pc-parity.py --write-doc docs/mobile-pc-parity.md`。
