# 手机独立章节建档回归

手机通过独立建档运行时完成“保存并建档”，保存 Gateway 地址不会改变执行位置。草稿生成先结束模型回合，由作者决定保存、建档和何时继续下一章。本文记录 3.4.4 的模型超时修复；后续资料操作、历史恢复及同步变化见 [手机独立操作验收](mobile-independent-authoring.md)。

## 模型请求与超时

3.4.3 的手机建档与立项对话都使用原生工具流式请求，但建档沿用了普通请求的 120 秒读取超时和 150 秒整次请求时限；持续输出超过 150 秒也会被截断。PC 建档按等待下一条有效流事件计时，连续 300 秒没有有效事件才超时，且 DeepSeek 建档明确关闭深度思考，手机此前缺少这项参数。截图中的 `timeout` 与这些差异相符，但仅凭截图无法确认服务端耗时或当时的思考模式。

当前权威配置由 PC 建档常量导出到 `cataloging.model_request`，Android 直接消费该契约：

- `stream_idle_timeout_seconds` 为 300 秒，表示等待第一条或下一条有效流事件的时限；正文、思考和有效工具参数会重新计时，持续输出允许超过 5 分钟。手机建档移除整次请求上限，由独立的事件计时器中断阻塞网络读取；注释心跳、空数据帧和只有角色标记的帧不会重置计时，本地进度回调耗时也不算模型等待时间。
- 温度为 0.1；输出上限为 20,000 tokens，手机同时遵守作者配置的较小输出预留。
- DeepSeek 建档在 Chat Completions 中发送 `thinking.type=disabled`，在 Responses 中发送 `reasoning.effort=none`，不改变其他任务的模型设置。两种参数的含义来自 [DeepSeek 官方思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/)。
- 进度根据真实流事件显示等待响应、分析资料、返回内容或接收工具参数，不再始终显示“核对建档计划”。
- 建档等待超时明确显示连续多久未收到有效输出，并将错误保存到任务；输出长度截断与 Responses 未完成事件属于模型响应错误。Responses 收到完成事件即停止等待；未完成回合不执行缓冲中的工具调用，也不自动重放请求。
- 取消直接终止正在等待数据的 HTTP 调用。失败保留正文和已接受候选；作者明确重试后复用前提未变化的计划，完成章节不重复建档。

本轮行为修复位于 Android。PC/Gateway 仅将既有温度和 DeepSeek 非思考策略提取为共享常量，执行行为未变；手机独立建档不要求 PC/Gateway 升级。

本轮对齐的是单次模型流的等待语义。PC 原有的有限重试与检查点恢复由模型网关处理；手机原生工具流中断后通过本机持久化计划由作者明确重试，尚未移植 PC 的流恢复协议，因此自动重试次数和等待过程仍有差异。两端对照测试关闭 PC 重试与恢复以比较同一轮流的完成边界，不将自动恢复策略列为已对齐。

## 自动化覆盖

2026-09-16 超时修复验证通过：353 项 Android JVM 测试、7 项 API 35 模拟器测试、64 项后端定向回归。提示词资产漂移、移动端能力契约和架构检查通过；Android Debug/测试构建及 Lint 通过。

- 同源契约：Android 构建资产直接导出 PC 工具目录、建档提示词、候选 schema、执行顺序、状态字段及长度限制。
- 两端业务结果：`contracts/fixtures/mobile-cataloging-v1.json` 是合成测试作品，同一计划分别经过 Android 资料投影与 PC 正式 applier；断言角色基础信息、状态、写作约束、版本、章/卷/场景绑定、角色关联和伏笔状态。
- Android JVM：不完整计划、其他作品的实体 ID、旧持有物保护、草稿按钮条件、正文版本推进，以及完整项目包的导出、导入、再次导出时保留角色和世界观关联。
- Android API 35 模拟器：无 Gateway 完成建档并解除下一章门槛；网络中断保留候选并明确重试；生成期间作者修改导致整章拒绝写入；取消与应用中断恢复；Room 3→5 迁移及旧版待同步正文的版本修复；连续正文保存、建档回执及后续作者编辑按顺序同步。
- 超时回归：`contracts/fixtures/agent-stream-idle-v1.json` 同时驱动 PC 实际 DeepSeek 适配器/网关和 Android MockWebServer，缩短等待时限，验证持续工具输出超过事件等待窗口仍完成、空心跳无效、中途停顿丢弃未完成工具缓冲、首个响应等待有界。另覆盖 Responses 持续输出与完成后连接未关闭、本地事件处理不消耗模型等待时限、普通请求保留原超时、作者可中断阻塞读取，以及两种 DeepSeek 协议的非思考参数。模拟器复现前 4 章已完成、第 5 章工具流中途超时，验证正式资料未发生部分写入，原候选保留，同一任务明确重试后完成。
- PC SQLite：过期正文、版本、档案和错误 ID 拒绝整章提交；失败没有部分领域写入；请求键重复不会重复更新版本或历史；同一失败请求在前提恢复后可以重试；新接口遵守已配对 Android 与已共享作品的边界。

## 复验命令

```powershell
backend\.venv\Scripts\python.exe scripts/export-mobile-prompt-contract.py
backend\.venv\Scripts\python.exe scripts/check-mobile-pc-parity.py --check-doc docs/mobile-pc-parity.md
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_mobile_prompt_contract.py backend/tests/test_cataloging.py backend/tests/test_mobile_cataloging_commit.py backend/tests/test_local_cli_cataloging_agent.py backend/tests/test_cataloging_stream_idle_parity.py backend/tests/test_stream_timeout_retry_boundary.py -q
backend\.venv\Scripts\python.exe backend/scripts/run_quality.py
mobile\android\gradlew.bat -p mobile/android :app:testDebugUnitTest :app:assembleDebug :app:assembleDebugAndroidTest :app:lintDebug --offline
```

在隔离模拟器中安装测试 APK 后执行 `MobileCatalogingInstrumentedTest` 和 `ProjectPackageChapterStateMigrationInstrumentedTest`。数据库测试使用内存数据库，网络同步测试使用本机 MockWebServer；没有读取或修改真实作品。

## 验证边界

这次自动化回归使用模拟的原生工具调用和网络故障，不代表真实模型端到端测试。既有 AI 实际运行测试主要基于 DeepSeek API 的 `deepseek-flash`；本次独立建档仍需用该模型在手机上验收。其他模型或 CLI 可能存在未知问题，不能将模拟测试解释为已经验证这些接入方式。

手机建档结果同步依赖 3.4.3 已提供的 PC/Gateway 建档接收适配器及授权路径；本轮超时修复未更改同步协议。
