# 司命当前架构（3.3.x）

本文描述当前 `main` 分支的运行架构。3.0 系列 ADR 记录了模块化迁移过程，仍用于解释已有约束，但不再代表项目仍处于 2.9 双路径迁移阶段。

司命是一个模块化单体：Windows 桌面版与 Gateway 共用同一个 FastAPI 应用、`/api/v1` 接口、SQLite 数据模型和业务服务；Android 通过相同 HTTP 契约或版本化离线同步契约复用这些能力。

## 运行形态

### Windows 桌面版

- `backend/launcher.py` 启动仅监听回环地址的 Uvicorn 服务，并用 PyWebView 打开打包后的 React 页面。
- 桌面运行时开放本地模型、OpenCode 等本机 CLI、MCP、计划任务和训练相关能力。
- PyInstaller 生成 onedir 负载，Inno Setup 生成正式发布的 `Siming-Setup.exe`。

### 用户自有 Gateway

- `SIMING_RUNTIME_PROFILE=gateway` 启用设备认证、配对、同步和远程创作入口。
- headless Gateway 使用同一 FastAPI 应用，但不注册本地模型、本机 CLI、MCP、技能管理和训练路由。
- Docker 镜像以非 root 用户运行，`/data` 保存 SQLite、作品镜像和 Gateway 身份材料。

### Android

- 在线时直接调用与 PC 前端相同的作品、章节、大纲、角色、世界观和治理 API。
- 离线时写入 Room 副本与 revision outbox；恢复连接后先 push 再 pull，冲突保留双方版本并交由用户选择。
- 无 Gateway 时，可使用构建阶段从 PC PromptSpec、工具目录和上下文策略导出的版本化契约运行手机独立 Agent。

## 数据与事务

SQLite 是唯一业务写入权威。Markdown/JSON 只作为可阅读镜像，不能通过直接改文件绕过数据库业务规则。

业务命令通过 Unit of Work 管理提交与回滚。章节、角色、大纲、世界观等写入会在同一事务中登记 `content_sync_jobs`；事务提交后，投影器再更新文件镜像。投影失败可重试，不会回滚已经成功提交的正文数据。

启动时由 Alembic 与数据库 bootstrap 识别结构、备份并迁移。无法安全识别的数据库进入只读恢复模式，HTTP 写请求会被拒绝，避免猜测性迁移损坏作品。

## 模块所有权

| 模块 | 当前职责 |
| --- | --- |
| `story` | 作品、章节、大纲、角色、世界观、版本快照和内容镜像 outbox |
| `creation` | 对话式立项、V3 Artifact、阶段生成、资料导入和正式建书 |
| `continuity` | 作品建档、叙事账本、故事粒度、候选事实和治理检查点 |
| `assistant` | 项目/系统对话、Agent 运行记录、记忆和未保存章节草稿 |
| `operations` | 长任务状态、事件、心跳、暂停、恢复、取消和失败诊断 |
| `model_runtime` | 任务模型选择、provider 就绪状态、API/CLI/本地模型执行 |
| `context` | ContextManifest、RAG、预算、来源证据和索引重建 |
| `integrations` | MCP、外部 Agent、Skill、提示词包以及导入导出集成 |
| `gateway` | 设备配对、令牌、同步修订、tombstone 和冲突解决 |

模块内部按四层增长：

1. `domain`：业务规则和值对象，不依赖框架。
2. `application`：命令、查询、端口与事务边界。
3. `interfaces`：HTTP、MCP、CLI 和事件适配器。
4. `infrastructure`：SQLAlchemy 仓储及外部实现。

`app.database.models` 等兼容导出仍可用于稳定旧导入路径，但兼容层不得形成第二套现行业务流程。新增或修改功能应更新当前权威实现，并同步移除废弃路由、状态机、提示词与测试。

## Agent 执行链路

1. 每个模型回合起始只开放 `set_tool_categories`。
2. 模型根据用户最新消息选择所需的宽粒度工具类别；运行时只验证类别并与当前授权、入口能力和真实注册表取交集。
3. 下一模型步骤获得统一 ToolSpec。PC GUI、API、本机 CLI、MCP、计划任务与 Android 构建契约均从同一工具目录投影。
4. 需要生成或评审的工具必须先创建或校验 ContextManifest，记录目标、预算、选中来源和模型身份。
5. 长任务写入统一 Operation/AgentRun 状态。SSE 连接只是订阅者；浏览器断线不会等价于取消，只有显式取消操作才终止后台任务。
6. 新章节生成成功后只保存为独立的未保存草稿并立即结束模型回合。作者确认后才能写入正式章节，并自行决定是否启动建档。

Agent 会话的完整 transcript 与运行步骤始终保留。每次模型调用由统一 ContextFrame 动态选择最近原文；较早的闭合回合可形成可重建 checkpoint。checkpoint 只用于历史导航，项目事实仍通过当前业务工具重读，未消费的原生工具调用和结果必须成对保留。具体边界见 [ADR 007](adr-007-agent-conversation-context.md)。

自然语言意图、目标实体和工具选择由模型结合真实数据完成。应用层只负责权限、项目归属、实体类型、结构校验、事务、幂等、并发和确定性的状态转换，不使用关键词或正则另建意图路由。

## 前端边界

- `app` 负责全局 provider 与应用组装。
- `shared` 只包含可复用 API、查询和 UI 基础设施，不能依赖 `app`、`features` 或 `pages`。
- `features` 封装可复用业务能力，不能依赖 `app` 或 `pages`。
- `pages` 负责页面组合；历史 `components`、`hooks`、`services` 等目录仍受依赖基线约束，不能反向依赖页面。
- TanStack Query 管理可复用服务端状态，Zustand 仅用于跨页面 UI 状态；OpenAPI 类型由 FastAPI schema 生成并在 CI 中检查漂移。

## 跨端一致性

Android/PC 能力状态以 `contracts/mobile-pc-parity.json` 为机器可读真源，`docs/mobile-pc-parity.md` 由检查脚本验证。新增 PC 路由、Android 写入实体或独立 Agent 工具时，必须同时记录在线、离线和独立运行语义以及幂等策略。

## 架构门禁

常用检查：

```text
python scripts/check-architecture.py
python scripts/check-mobile-pc-parity.py --check-doc docs/mobile-pc-parity.md
python scripts/export-mobile-context-policy.py --check

cd backend
python scripts/run_quality.py
python ../scripts/run-backend-tests-isolated.py tests

cd ../frontend
npm run quality
npm test
npm run build
```

门禁禁止循环导入、路由直接访问 ORM、路由直接提交事务、模块反向依赖和生成契约漂移。存量超大文件由 ratcheting baseline 管理：不能继续增长，并应在相关变更中逐步拆分。

## 相关决策与运维文档

- [ADR 001：模块化单体](adr-001-modular-monolith.md)
- [ADR 002：事务与迁移](adr-002-transactions-and-migrations.md)
- [ADR 003：3.0 兼容迁移历史](adr-003-compatibility-strangler.md)
- [ADR 004：运行时边界](adr-004-runtime-boundaries.md)
- [ADR 005：Prompt 与 Tool 契约](adr-005-prompt-and-tool-contracts.md)
- [ADR 006：前端状态与契约](adr-006-frontend-state-and-contracts.md)
- [ADR 007：Agent 会话上下文与原生工具边界](adr-007-agent-conversation-context.md)
- [数据库恢复](../operations/database-recovery.md)
- [Android/PC 能力一致性](../mobile-pc-parity.md)
- [Gateway 威胁模型](../security/gateway-threat-model.md)
