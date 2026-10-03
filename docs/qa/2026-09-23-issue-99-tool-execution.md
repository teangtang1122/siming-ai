# Issue #99：立项工具写入与调用记录

关联问题：[关于模型调用问题 #99](https://github.com/teangtang1122/siming-ai/issues/99)。

## 已复现与修复

- 已有人物资料时，不传 `entity_id` 调用 `generate_creation_artifact`、`refine_creation_artifact` 或 `regenerate_creation_artifact`，模型可以返回有效的新人物和关系。原服务端合并逻辑却用旧集合覆盖这些新内容，再返回 `ok / saved=true`。现在整份资料以已有资料为输入，保存新的生成内容；单实体调用仍只合并模型指定的目标。
- 人物、地点、世界观、卷纲、细纲等资料阶段整份生成时，统一校验作者锁定的 JSON Pointer。模型及同模型结构修复仍修改锁定值时，返回结构化失败，数据库资料和修订号保持不变。PC 与手机独立运行遵循相同契约；创意方向继续使用原有的独立生成契约。
- 立项阶段的调用记录关联到真实 `run_id`、`operation_id`。后台单独运行也建立记录；同一回合多次调用的历史关联都可查询。旧诊断数据库升级时从已有事件重建关联索引，不能恢复原先没有记录的信息。
- 执行器返回 `failed`、`conflict` 等明确失败状态时，诊断步骤显示失败。重启后的孤立记录及旧版遗留的“采集中断但仍运行”记录标记为采集中断。
- PC 与手机的步骤展示区分运行中、采集中断、缺少结束记录和未加载后续记录；未收到耗时就显示未知，实际小于一毫秒显示 `<1 ms`。

## 验证

- `backend/tests/test_creation_artifact_execution.py`：固定模型响应，执行真实工具入口和 SQLite 写入，覆盖三种工具、锁保护、持久任务状态及记录关联。
- `backend/tests/test_context_trace_contract.py`、`test_context_trace_capture.py`、`test_context_trace_access.py`：失败回执、诊断数据库迁移、多个任务关联及访问隔离。
- `frontend/src/__tests__/ContextInspector.test.tsx`：终止记录、分页未加载、真实失败状态与耗时展示。
- Android `MobileCreationConversationAgentTest`：使用本地模拟模型服务完成独立工具调用，不依赖 PC，覆盖已有资料更新与锁保护。
- Android `ContextTraceTest`、`ContextTraceRecoveryInstrumentedTest`：状态展示、失败回执及真实 Room 数据库恢复。

这些测试替换的是模型网络响应，业务执行与持久化走正式实现。它们确认了以上代码缺陷，尚不能证明 issue 作者的第三方 API 故障全部由这些缺陷造成；供应商请求失败和模型总结失败仍需对应调用记录定位。

验证结果：相关后端回归、后端架构与质量检查、前端 7 项测试、TypeScript 与修改文件的 ESLint 检查通过；Android 354 项单元测试及 Android 15 模拟器上的 1 项 Room 恢复测试通过。改动尚未发布。
