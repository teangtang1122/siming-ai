# 手机独立章节建档回归

本次修改解决无 Gateway 时“保存并建档”被禁用，以及正文保存后缺少独立建档运行时的问题。草稿生成仍先结束模型回合，由作者决定保存、建档和何时继续下一章。

## 自动化覆盖

2026-09-16 本地验证通过：344 项 Android JVM 测试、6 项 API 35 模拟器测试、112 项后端回归。提示词资产漂移、移动端能力契约和架构检查通过；Android 测试构建通过。

- 同源契约：Android 构建资产直接导出 PC 工具目录、建档提示词、候选 schema、执行顺序、状态字段及长度限制。
- 两端业务结果：`contracts/fixtures/mobile-cataloging-v1.json` 是合成测试作品，同一计划分别经过 Android 资料投影与 PC 正式 applier；断言角色基础信息、状态、写作约束、版本、章/卷/场景绑定、角色关联和伏笔状态。
- Android JVM：不完整计划、其他作品的实体 ID、旧持有物保护、草稿按钮条件、正文版本推进，以及完整项目包的导出、导入、再次导出时保留角色和世界观关联。
- Android API 35 模拟器：无 Gateway 完成建档并解除下一章门槛；网络中断保留候选并明确重试；生成期间作者修改导致整章拒绝写入；取消与应用中断恢复；Room 3→5 迁移及旧版待同步正文的版本修复；连续正文保存、建档回执及后续作者编辑按顺序同步。
- PC SQLite：过期正文、版本、档案和错误 ID 拒绝整章提交；失败没有部分领域写入；请求键重复不会重复更新版本或历史；同一失败请求在前提恢复后可以重试；新接口遵守已配对 Android 与已共享作品的边界。

## 复验命令

```powershell
backend\.venv\Scripts\python.exe scripts/export-mobile-prompt-contract.py
backend\.venv\Scripts\python.exe scripts/check-mobile-pc-parity.py --check-doc docs/mobile-pc-parity.md
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_mobile_cataloging_commit.py backend/tests/test_android_canonical_routes.py -q
mobile\android\gradlew.bat -p mobile/android :app:testDebugUnitTest :app:assembleDebug :app:assembleDebugAndroidTest --offline
```

在隔离模拟器中安装测试 APK 后执行 `MobileCatalogingInstrumentedTest` 和 `ProjectPackageChapterStateMigrationInstrumentedTest`。数据库测试使用内存数据库，网络同步测试使用本机 MockWebServer；没有读取或修改真实作品。

## 验证边界

这次自动化回归使用模拟的原生工具调用和网络故障，不代表真实模型端到端测试。既有 AI 实际运行测试主要基于 DeepSeek API 的 `deepseek-flash`；本次独立建档仍需用该模型在手机上验收。其他模型或 CLI 可能存在未知问题，不能将模拟测试解释为已经验证这些接入方式。

本次同时修改 Android 和 PC/Gateway 的建档接收适配器及授权路径。手机独立建档本身不需要 PC；将手机建档结果同步到 PC 时，两端需要包含本次改动。
