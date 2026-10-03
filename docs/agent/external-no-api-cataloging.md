# 外部 Agent 建档

更新日期：2026-09-26。本文描述当前唯一建档路径。

司命以数据库为资料权威来源。API、托管 CLI、外部 MCP 和手机连接的服务共用同一建档工具契约、计划校验和整章应用器。外部模型可以独立生成计划，不需要配置司命模型 API；手机端连接自己的服务，不依赖 PC 客户端界面运行。

每章由同一个 Agent 读取正文和实际档案，决定身份、场景及需要更新的资料，增量保存一份计划。计划完整后进行确定性校验和整章事务写入。一个决策阶段可以包含多次模型请求和工具调用，并不等于只请求一次模型。

## 执行顺序

1. 托管建档由系统直接开放当前任务工具，无需选择类别。普通外部 MCP 按其权限包开放工具；通过 `get_prompt_pack(pack_id="cataloging_external_no_api")` 取得共享规则。
2. 已有托管任务直接使用其 `project_id`、`job_id`、`chapter_id`；无任务时调用 `start_external_cataloging_job`。所有调用绑定同一作品。
3. `get_next_external_cataloging_chapter(job_id)` 返回当前可处理章节的真实 ID、正文版本、完整正文、只读镜像路径及恢复摘要。该工具不再接受 `phase` 参数。外部模型必须阅读完整正文。
4. 按需调用 `read_cataloging_archive(kind=...)` 分页读取人物、设定、大纲、关系索引。省略 `ids` 读取索引；指定至多 5 个真实 ID 取得完整资料。翻页使用返回的 `next_arguments`。仅当前作品的有效资料可用。
5. 调用 `save_external_cataloging_candidates`。先提交 `chapter_summary` 计划，后提交依赖它的变更；可在同一原生数组内按此顺序提交。中间批次 `finalize=false`，完成时 `finalize=true`。字段和 `type` 在同一层，禁止 JSONL 或将数组序列化成字符串。
6. 只有返回 `candidate_set_complete=true` 才进入应用。普通外部任务按作者授权调用 `apply_pending_cataloging`；托管 CLI 的 auto 模式由提交工具自动应用并返回 `auto_applied=true`，此时不重复应用。manual 模式等待作者确认。
7. `verify_external_cataloging_progress` 验证当前章结果。前一章完成后才处理下一章；最后通过 `get_project_archive_status` 验证目标作品的实际数据。

禁止在此流程调用 `start_cataloging_job` 等司命内部模型生成器；`start_cataloging_job` 属于用户选择内部 API 时的入口。

## 计划和字段

`chapter_summary` 必须包含 `summary_text`、非空 `scenes`、`character_bindings`、`worldbuilding_bindings`、`coverage_manifest`、`narrative_state`、`narrative_review`。`coverage_manifest.scene_count` 等于 `scenes` 长度。

每个正式人物或设定声明 `{name, id, decision, reason, source_labels?}`。`decision="existing"` 使用当前档案真实 ID 和正式名称；`decision="new"` 使用模型生成的规范 UUID，并在对应创建候选的 `client_id` 复用。应用层只验证 ID、归属、类型和结构，身份含义由模型判断。身份未确定的称呼可以留在摘要或场景叙述中。

字段示例：`profile.reveal_chapter` 为整数或 `null`，`items_or_assets` 为字符串，`importance` 为 `major|normal|minor`，`role_type` 为 `protagonist|supporting|antagonist|mentor|other`。自然语言身份写入 `background`。原始数组、枚举及必填字段以实际工具 JSON Schema 为准，服务不会猜测转换错误字段。

每章一个章级大纲；已有绑定使用该真实 ID。多场景时提交 `scene_number=1..N` 的 section 节点。大纲角色关联使用计划中的 `character_ids`。`parent_id` 只能为真实大纲 ID；系统不会按标题或章节数字改绑到另一章。

## 修正和一致性

- 参数错误返回字段路径、规则和允许值；计划错误返回 `candidate_errors`、`missing_required_items`。同一 Agent 只修正失败对象，已暂存候选继续保留。可通过 `list_cataloging_candidates` 分页取得完整记录。
- 修改摘要覆盖范围时提交完整计划并设置 `coverage_manifest_mode="replace"`；修改聚合章节关联使用 `chapter_link_mode="replace"` 并提供全部关联数组。撤回选错的未应用候选使用 `reject_candidate_ids`；作者编辑或确认过的内容不能被模型覆盖或撤回。
- 连续失败或无有效保存进展时停止本轮，保留检查点。`repair_cataloging_plan_current` / HTTP `repair-plan-current` 保留计划继续修正；完整重试仍是显式操作。
- 正式摘要、人物、设定、关系、大纲、日志及资料对账在同一个章节事务中应用。任何一项失败，全章回滚；不产生半份新建档。正文版本变化、暂停、取消、跨作品 ID 都会阻止写入。
- 升级时未完成的旧检查点暂停等待统一计划修正。历史候选、作者编辑和审计资料保留，旧事实记录不再替当前模型决定身份。已完成的历史结果保持可查。

## 契约来源与验证

字段定义：`backend/app/modules/continuity/domain/candidate_contract.py`；计划与 ID 校验：`backend/app/services/cataloging/plan_contract.py`；共享提示词：`backend/app/prompts/cataloging_source.py`。

修改共享规则后运行 `scripts/export-cataloging-prompts.py` 和 `scripts/export-mobile-prompt-contract.py`。验收覆盖原生 API 工具循环、CLI/MCP 独立逐章流程、PC/手机共用服务控制，以及暂停、恢复、作者确认、失败回滚和幂等。
