# Android ↔ PC 能力对齐契约

> 本文由 `contracts/mobile-pc-parity.json` 通过 `scripts/check-mobile-pc-parity.py` 生成；请勿手工修改。

PC 定义共享业务契约，Android 在手机执行对应领域操作。保存 Gateway 地址不改变手机 API 与本地资料的执行位置；跨设备同步和作者显式选择的远程模型才使用 Gateway。平台差异及验证范围在各项能力中说明。

当前共登记 **28** 项能力：**13** 项已对齐、**15** 项部分对齐、**0** 项待实现。

## 总览

| 能力 | 权威入口 | Android 在线 | Android 离线 | Android 独立 Agent | 状态 |
|---|---|---|---|---|---|
| `assistant.workspace` | workspace assistant stream / MobileWorkspaceAgent | 明确降级实现 | 明确阻止 | 明确降级实现 | 部分对齐 |
| `authoring.chapter` | /api/v1/projects/{project_id}/chapters | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 已对齐 |
| `authoring.character` | /api/v1/projects/{project_id}/characters | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 已对齐 |
| `authoring.document_import` | /api/v1/import/project-file | 本地副本 | 本地副本 | 本地副本 | 部分对齐 |
| `authoring.exchange_package` | /api/v1/projects/project-package/import | 手机独立领域实现 | 本地副本 | 本地副本 | 已对齐 |
| `authoring.export` | /api/v1/projects/{project_id}/export | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 部分对齐 |
| `authoring.outline` | /api/v1/projects/{project_id}/outline | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 已对齐 |
| `authoring.project` | /api/v1/projects/{project_id} | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 已对齐 |
| `authoring.worldbuilding` | /api/v1/projects/{project_id}/worldbuilding | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 已对齐 |
| `chapter.cataloging` | /api/v1/projects/{project_id}/cataloging | PC 权威实现 | 明确阻止 | PC 权威实现 | 部分对齐 |
| `chapter.history` | GET /chapters/{chapter_id}/snapshots[/diff/{snapshot_id}] | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 已对齐 |
| `chapter.reorder` | PUT /api/v1/projects/{project_id}/chapters/reorder | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 已对齐 |
| `chapter.restore` | POST /chapters/{chapter_id}/restore/{snapshot_id} | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 已对齐 |
| `character.ai_config` | GET/PUT /characters/{character_id}/ai-config | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 部分对齐 |
| `character.relationships` | GET /characters/relationships; PUT /characters/{character_id}/relationships | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 已对齐 |
| `character.versions` | GET /characters/{character_id}/versions[/{version_id}] | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 部分对齐 |
| `context.inspector` | siming.context_trace.v1 | 调用 PC 权威接口 | 本地副本 | 调用 PC 权威接口 | 已对齐 |
| `context.selection` | prepare_task_context -> search_task_context -> submit_context_evidence | 调用 PC 权威接口 | 明确阻止 | 明确降级实现 | 部分对齐 |
| `governance.items` | /narrative-governance/items[/{type}/{id}] | 手机独立领域实现 | 手机独立领域实现 | 明确降级实现 | 部分对齐 |
| `novel_creation.session` | /api/v1/novel-creation/* | 明确降级实现 | 手机独立领域实现 | 明确降级实现 | 部分对齐 |
| `sync.conflicts` | POST /api/v1/sync/conflicts/{conflict_id}/resolve | 调用 PC 权威接口 | 明确阻止 | 不适用 | 已对齐 |
| `sync.replication` | /api/v1/sync/{bootstrap,push,pull} | 调用 PC 权威接口 | 手机独立领域实现 | 手机独立领域实现 | 已对齐 |
| `worldbuilding.history` | GET /worldbuilding/{entry_id}/{versions\|timeline} | 手机独立领域实现 | 手机独立领域实现 | 手机独立领域实现 | 部分对齐 |
| `worldbuilding.relationships` | WorldbuildingRelation sync record | 修订队列回放 | 修订队列回放 | 尚未支持 | 部分对齐 |
| `writer.chapter` | chapter_writer | 明确降级实现 | 明确阻止 | 明确降级实现 | 部分对齐 |
| `writer.character` | character_writer | 明确降级实现 | 明确阻止 | 明确降级实现 | 部分对齐 |
| `writer.outline` | outline_writer | 明确降级实现 | 明确阻止 | 明确降级实现 | 部分对齐 |
| `writer.worldbuilding` | worldbuilding_writer | 明确降级实现 | 明确阻止 | 明确降级实现 | 部分对齐 |

## 详细能力

### `assistant.workspace` — 统一的工作区 Agent 对话与工具循环

- **权威入口：** `workspace assistant stream / MobileWorkspaceAgent`（`pc_workspace_tool`）
- **状态：** 部分对齐
- **副作用：** 无写入副作用
- **幂等策略：** `not_applicable`；只读或无需防重
- **PC：** PC 权威实现
- **Android 在线：** 明确降级实现：提示词、工具 schema、宽粒度类别控制器与版本化上下文策略均由 PC 源生成；模型正文、推理和函数参数使用供应商 SSE 真流式解析，thinking 模型沿用 PC 的 provider-safe tool_choice 规则并在接口明确拒绝时于输出前自动去参重试，章节草稿、完整 ContextManifest、通用会话转录与工具日志均可跨重启恢复。独立运行仍不进入 PC 的数据库级 AgentRun 审计，检索仍是确定性词法降级。
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
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。

### `authoring.character` — 角色卡创建、读取、更新和删除

- **权威入口：** `/api/v1/projects/{project_id}/characters`（`pc_http`）
- **状态：** 已对齐
- **副作用：** character_version、alias_sync、content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。

### `authoring.document_import` — 小说 TXT / Markdown / DOCX 批量导入与章节拆分

- **权威入口：** `/api/v1/import/project-file`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** content_sync
- **幂等策略：** `client_serialization`；必须防重
- **幂等限制：** Android 一次只提交一个导入文件；服务端当前未接收独立 request key。
- **PC：** PC 权威实现
- **Android 在线：** 本地副本：本机直接解析 TXT / Markdown / DOCX，文本与 PC 共用多编码回归样本且严格拒绝替换字符；Markdown 标题只参与章节边界识别，作品和章节在同一本地事务中写入后进入 outbox。
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
- **Android 在线：** 手机独立领域实现：先在手机完成导入和导出；可选同步上传原包并回放后续修订。
- **Android 离线：** 本地副本：先流式落盘并完成协议校验，再恢复可编辑副本；完整原包、请求键和暂不展示的合法集合持续保留，联网后先上传项目包再回放普通 outbox。
- **Android 独立 Agent：** 本地副本：可从保留的完整原包叠加本机最新作者资料并生成相同 v1 格式；暂不展示的集合和素材不会丢失，也不会启动 Agent、建档或自动任务。

### `authoring.export` — 小说 TXT / Word / PDF 导出与本机保存

- **权威入口：** `/api/v1/projects/{project_id}/export`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** 无写入副作用
- **幂等策略：** `not_applicable`；只读或无需防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：TXT、Word（DOCX）、PDF 均由手机生成，按正式章节顺序完整导出正文。
- **Android 离线：** 手机独立领域实现：TXT、Word（DOCX）、PDF 均由手机生成，按正式章节顺序完整导出正文。
- **Android 独立 Agent：** 手机独立领域实现：TXT、Word（DOCX）、PDF 均由手机生成，按正式章节顺序完整导出正文。
- **已知缺口：**
  - Android PDF 使用系统字体与原生分页，PC 使用桌面导出引擎；排版和字体可能不同，文本、章节顺序与格式可用性保持一致。

### `authoring.outline` — 大纲树创建、读取、更新、删除和同级排序

- **权威入口：** `/api/v1/projects/{project_id}/outline`（`pc_http`）
- **状态：** 已对齐
- **副作用：** cycle_validation、character_link_replace、content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。

### `authoring.project` — 作品创建、读取、更新和删除

- **权威入口：** `/api/v1/projects/{project_id}`（`pc_http`）
- **状态：** 已对齐
- **副作用：** content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。

### `authoring.worldbuilding` — 世界观条目创建、读取、更新和删除

- **权威入口：** `/api/v1/projects/{project_id}/worldbuilding`（`pc_http`）
- **状态：** 已对齐
- **副作用：** world_version、content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。

### `chapter.cataloging` — 已保存章节的独立 API 建档、整章事务与后续 PC 同步

- **权威入口：** `/api/v1/projects/{project_id}/cataloging`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** cataloging
- **幂等策略：** `client_serialization`；必须防重
- **幂等限制：** 本机按章节内容哈希、版本和来源快照恢复同一未完成计划；完成状态与资料同一 Room 事务写入。PC 接收已完成计划时按 request_id 去重，经同一 applier 校验并应用，不再调用模型。
- **PC：** PC 权威实现
- **Android 在线：** PC 权威实现：使用手机 API 和 PC 同源工具目录、提示词及候选契约，完成角色、设定、场景大纲、章节关联与治理资料的本机事务。草稿须先由作者明确保存并建档。
- **Android 离线：** 明确阻止：完全断网时不能调用模型；正文、建档计划和错误保存在本机，恢复 API 网络后可明确重试。
- **Android 独立 Agent：** PC 权威实现：使用手机 API 和 PC 同源工具目录、提示词及候选契约，完成角色、设定、场景大纲、章节关联与治理资料的本机事务。草稿须先由作者明确保存并建档。
- **已知缺口：**
  - 手机任务保存在 Room，可取消与明确重试；未同步前不会显示在 PC 任务中心。手机通过最终计划完整性校验后自动应用，没有 PC 的手工逐候选编辑模式。
  - 同步手机建档回执需要同时更新 PC/Gateway；旧 Gateway 无此接收接口时会明确报错并保留手机结果。
  - Android 对非法治理引用会终止最终提交并交还模型修正；模型连续失败后保留正文及候选，不把该章标记为已建档。

### `chapter.history` — 章节快照列表、详情与差异比较

- **权威入口：** `GET /chapters/{chapter_id}/snapshots[/diff/{snapshot_id}]`（`pc_http`）
- **状态：** 已对齐
- **副作用：** 无写入副作用
- **幂等策略：** `read_only`；只读或无需防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。

### `chapter.reorder` — 章节权威阅读顺序重排

- **权威入口：** `PUT /api/v1/projects/{project_id}/chapters/reorder`（`pc_http`）
- **状态：** 已对齐
- **副作用：** authoritative_reorder、content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。

### `chapter.restore` — 从历史快照恢复章节及关联叙事状态

- **权威入口：** `POST /chapters/{chapter_id}/restore/{snapshot_id}`（`pc_http`）
- **状态：** 已对齐
- **副作用：** chapter_restore、chapter_snapshot、ledger_restore、governance_invalidation、cataloging、content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：恢复生成新的正文版本，回退受影响建档来源并要求重新建档。只恢复本机实际保存或导入的历史，不生成缺失历史。

### `character.ai_config` — 角色专用语气、口头禅与模型配置

- **权威入口：** `GET/PUT /characters/{character_id}/ai-config`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **已知缺口：**
  - 角色配置页可在手机独立修改；角色扮演模型运行能力不属于普通章节写作，当前未作为独立移动工作流提供。

### `character.relationships` — 有方向的角色关系网读取与替换

- **权威入口：** `GET /characters/relationships; PUT /characters/{character_id}/relationships`（`pc_http`）
- **状态：** 已对齐
- **副作用：** relationship_replace、content_sync
- **幂等策略：** `revisioned_outbox`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。

### `character.versions` — 角色版本列表与历史快照查看

- **权威入口：** `GET /characters/{character_id}/versions[/{version_id}]`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** 无写入副作用
- **幂等策略：** `read_only`；只读或无需防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **已知缺口：**
  - Android 当前只读查看，尚未提供角色历史恢复。

### `context.inspector` — 本机上下文调用记录、按消息查看与脱敏诊断包

- **权威入口：** `siming.context_trace.v1`（`shared_generated_contract`）
- **状态：** 已对齐
- **副作用：** diagnostic_store
- **幂等策略：** `set_replacement`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 调用 PC 权威接口
- **Android 离线：** 本地副本
- **Android 独立 Agent：** 调用 PC 权威接口

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
- **Android 在线：** 手机独立领域实现：治理页独立校验解决章节、复检说明及状态迁移，并保存生命周期事件。
- **Android 离线：** 手机独立领域实现：治理页独立校验解决章节、复检说明及状态迁移，并保存生命周期事件。
- **Android 独立 Agent：** 明确降级实现：可读取最新治理锁并用于写章，但独立 Agent 未开放治理项创建、复检和关闭工具。
- **已知缺口：**
  - 手机独立 Agent 目前只消费治理锁，不执行治理生命周期命令。

### `novel_creation.session` — 对话式立项会话、阶段确认和正式归档

- **权威入口：** `/api/v1/novel-creation/*`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** project_archive、structured_entities、content_sync
- **幂等策略：** `request_key`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 明确降级实现：使用与 PC 构建时导出的 V3 PromptSpec、八个完整阶段（包含创作约束）、标准化、依赖失效和最终建档门槛；结构化建档页中的创意选择、阶段生成/编辑/确认、最终审阅和正式建档体验与 PC 对齐。手机独立执行仍没有 PC 服务端 durable Operation/AgentRun 审计记录。
- **Android 离线：** 手机独立领域实现：已保存的立项资料可以本机编辑、确认并建立作品；AI 生成需要手机 API。旧 PC 草稿可显式转为手机独立立项。
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
- **Android 离线：** 手机独立领域实现：正文版本、治理迁移和高级操作按顺序持久化；不确定回执使用同一 ID 重试。衍生记录与正文在同一事务同步，成功后核对完整副本。
- **Android 独立 Agent：** 手机独立领域实现：正文版本、治理迁移和高级操作按顺序持久化；不确定回执使用同一 ID 重试。衍生记录与正文在同一事务同步，成功后核对完整副本。

### `worldbuilding.history` — 世界观版本与时间线查看

- **权威入口：** `GET /worldbuilding/{entry_id}/{versions|timeline}`（`pc_http`）
- **状态：** 部分对齐
- **副作用：** 无写入副作用
- **幂等策略：** `read_only`；只读或无需防重
- **PC：** PC 权威实现
- **Android 在线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 离线：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
- **Android 独立 Agent：** 手机独立领域实现：本机读取或在 Room 事务中写入；无需连接 PC。跨设备同步另行执行，保留冲突保护。
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
- **Android 在线：** 明确降级实现：写章前创建或校验 ContextManifest，并在生成开始前一次性消费选择令牌；生成前持久化运行、成功后原子保存草稿与完整清单。同一请求使用确定性 run ID，应用重启和断流恢复到同一未保存草稿。正式保存与建档由作者操作。检索仍为 Android 本地词法降级，审计未上传到 PC 账本。
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
- **Android 在线：** 明确降级实现：提示词和输出工具由 PC 构建时导出，但上下文检索和后续领域副作用在手机端执行。
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
- **Android 在线：** 明确降级实现：使用 PC 导出的提示词、输出工具和 outline_planning 策略，生成开始前一次性消费选择令牌，并持久化单一 pending 提案；支持跨重启恢复、编辑、确认、确认后新回合写作、重新生成和放弃。提案超量或结构非法时整体拒绝，不静默截断；确认时按卷、章、节的依赖顺序写入。确认前不写正式大纲，并以正式大纲指纹拒绝过期提案。
- **Android 离线：** 明确阻止：没有手机直连模型时不运行生成器。
- **Android 独立 Agent：** 明确降级实现：使用 PC 导出的提示词、输出工具和 outline_planning 策略，生成开始前一次性消费选择令牌，并持久化单一 pending 提案；支持跨重启恢复、编辑、确认、确认后新回合写作、重新生成和放弃。提案超量或结构非法时整体拒绝，不静默截断；确认时按卷、章、节的依赖顺序写入。确认前不写正式大纲，并以正式大纲指纹拒绝过期提案。
- **已知缺口：**
  - 手机使用本地词法检索，ContextManifest 与大纲提案保存在手机；服务端数据库审计属于显式远程模型路线。

### `writer.worldbuilding` — 生成结构化世界观条目

- **权威入口：** `worldbuilding_writer`（`pc_workspace_tool`）
- **状态：** 部分对齐
- **副作用：** draft_store
- **幂等策略：** `draft_reference`；必须防重
- **PC：** PC 权威实现
- **Android 在线：** 明确降级实现：提示词和输出工具共享，但未消费世界观关系边，也没有 PC 上下文清单审计。
- **Android 离线：** 明确阻止：没有手机直连模型时不运行生成器。
- **Android 独立 Agent：** 明确降级实现：提示词和输出工具共享，但未消费世界观关系边，也没有 PC 上下文清单审计。
- **已知缺口：**
  - 手机独立世界观生成尚未消费 WorldbuildingRelation 或 ContextManifest。

## 维护规则

1. 新增 `PcApiPaths` 方法、`PcAuthoringContract` 可写类型或 `MobileWorkspaceAgent` 工具时，必须在契约中做出能力归属或写明忽略理由。
2. 声称已实现的模式必须引用实际源码；每项能力必须引用至少一个回归测试。
3. `degraded`、`unsupported` 或高风险写操作必须明确限制和幂等策略，不能以“行为大致相同”代替契约。
4. 修改契约后运行：`python scripts/check-mobile-pc-parity.py --write-doc docs/mobile-pc-parity.md`。
