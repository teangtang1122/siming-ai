# Siming Android

Android 客户端位于 `mobile/android`，最低 Android 8.0（API 26），目标 API 35。它保存可写离线副本，可直接连接用户选择的 OpenAI 兼容 API，也可连接用户自有 Gateway 做跨设备同步；不包含本地模型、OpenCode、CLI、MCP 或训练运行时。

## AI 与 PC 一致性契约

- 首次启动可选择“稍后配置”直接进入本机作品库，导入、查看和手动编辑资料无需 API 或 Gateway。这个选择会持久保存；需要 AI 或跨设备同步时，再从设置配置直连 API 或连接自己的 Gateway。
- 作品、章节、大纲、角色和世界观统一在手机的 Room 事务中增删改，并记录可选同步队列。连接状态不决定业务路径。历史、差异比较、章节恢复、排序、角色关系/AI 配置及世界观版本和时间线均从本机资料完成。
- 项目助手默认使用手机 API；同时配置 Gateway 时可显式选择 PC 模型。手机 API 的提示词、工具执行和存储在手机完成，Key 不因保存了 Gateway 地址而发送给 PC。远程模型会话与跨设备同步需要其主机运行。
- `scripts/export-mobile-prompt-contract.py` 从 PC `PromptSpec`、写作规则和工具注册表生成 `pc_workspace_prompt_contract.json`；手机运行同源函数调用循环和章节/角色/大纲/世界观二级生成器，本地工具写入手机数据库与 outbox。
- “AI 立项”复用 PC `creation.novel.stage@3.0.0` 的预设、动态采访、8 阶段 PromptSpec、影响依赖、JSON 修复与数据契约。手机 API 始终在 Android 执行，不会因 Gateway 已配置而改为 PC 代办；作者显式选择 PC 模型时才使用远程会话。
- 旧 PC 立项草稿可以从手机已保存的资料“转为手机独立立项”，继续编辑、确认并建立作品。立项草稿可直接从手机移除，后续远程刷新不会自动恢复它。
- 核心立项资料通过最终审阅后即可“建立正式作品档案”，前三章细纲与 PC 一样可以建档前确认，也可以稍后完善。Gateway 路线调用 PC `/apply`；纯手机路线按相同实体字段建立作品、角色关系、世界设定关系、卷纲以及已确认的章节/场景细纲，并进入离线同步队列。
- 外部小说导入与 PC 共用 TXT / Markdown / DOCX 格式边界和 20 MiB 上限。在本机安全解码文本或提取 DOCX 正文，再按与 PC 一致的章节标题识别规则原子保存，不会用错误编码替换字符强行导入。
- 司命项目包使用独立 `.siming-project` 入口和磁盘流式处理，不经过小说 `ByteArray` 解析器。离线导入保留完整原包与幂等请求键；联网时先上传项目包，再回放该作品的普通 outbox。
- 删除作品始终移除当前手机副本、原包和待同步记录，其他设备的副本保留；本机排除记录防止作品被自动重新下载。已经同步或上传中断的作品也能本地删除。删除、项目包导入与同步串行执行。
- TXT、Word（DOCX）和 PDF 均由手机导出，包含按阅读顺序排列的正式章节。PDF 使用 Android 字体和分页，与 PC 的具体版式可能不同。
- 保存正文、恢复版本和治理状态变更在本机维护历史、检查点和建档失效状态。章级恢复会回退受影响的建档来源，并要求重新建档；作者后续手动改动不会被旧生成值覆盖。已导入包只提供其实际保留的历史，不补造缺失历史。
- 高级操作使用稳定命令 ID 与版本/实体守卫按顺序同步到 PC 相同领域服务。网络中断后重试同一 ID；同步失败保留手机结果，PC/Gateway 需升级后才能接收新增命令。
- 项目包首次同步以导入回执记录的初始版本为基准，既允许回放手机离线修改，也保留 PC 在导入后产生的真实版本冲突。
- 项目包在手机独立模式下直接恢复大纲、世界观、伏笔及叙事治理副本，使用与 PC 相同的记录类型；卷章层级、角色关联和治理引用按统一 UUIDv5 规则重建。版本、检查点与当前资料分别识别，导出时按具体记录类型归入原集合。`project-package-v1-authoring.json` 是两端导入回归测试共用的虚构数据，不包含用户作品。
- 完整项目包按同章、当前版本正文快照和后续建档摘要恢复续写门槛，与 PC 共用 `project-package-v1-chapter-state.json` 验证契约。升级会一次性修复已导入作品缺失的状态，并保留正文修改及已知的待建档状态；不会要求已具备证据的手机副本先连接 PC。
- `backend/tests/test_mobile_prompt_contract.py` 会重建并比较该资产。PC 提示词或工具 schema 改动后若没有重新导出，测试会失败，防止手机悄悄退化成简化提示词。

## 开发构建

Debug 安装包显示为“司命（测试版）”，使用独立应用标识与数据目录，可以覆盖旧测试版；从正式版迁入测试时需要重新导入项目，API 配置也独立保存。

```powershell
$env:JAVA_HOME = "C:\path\to\jdk-17"
$env:ANDROID_SDK_ROOT = "C:\path\to\android-sdk"
cd mobile\android
.\gradlew.bat testDebugUnitTest lintDebug assembleDebug
```

Debug APK 位于 `app/build/outputs/apk/debug/app-debug.apk`。

## 正式签名

正式 APK 必须始终使用同一发布密钥。密钥文件、口令、生成的 APK、截图、模拟器数据和 `local.properties` 均被 `.gitignore` 排除；不要通过 Issue、日志或 Release 上传密钥。

```powershell
$env:SIMING_ANDROID_KEYSTORE_FILE = "C:\secure\siming-release.jks"
$env:SIMING_ANDROID_KEYSTORE_PASSWORD = "..."
$env:SIMING_ANDROID_KEY_ALIAS = "siming"
$env:SIMING_ANDROID_KEY_PASSWORD = "..."
.\scripts\build-android-release.ps1
```

脚本执行 R8 release 构建、zipalign、APK Signature Scheme 签名、签名/包名/版本验证，并输出 `release/Siming.apk` 与 `release/Siming-apk-sha256.txt`。GitHub Release 的 APK 版本必须与 `backend/app/version.py` 和 `frontend/package.json` 一致。

## 发布前检查

- 单元测试、lint、Debug 与 Release 构建通过。
- 运行 `python scripts/export-mobile-prompt-contract.py`，并确认提示词漂移测试通过。
- 在实际模拟器或手机检查直连 API 配置与模型自动获取、连接、扫码、作品库、新建/导入、编辑、离线、同步、冲突、AI 禁用/运行和关于页面。
- 分别验证手机 API（无 Gateway、有不可达 Gateway）及显式 PC 模型路线；相同输入应遵循同一工具参数结构和实体字段。独立运行验收见 [测试说明](../docs/testing/mobile-independent-authoring.md)。
- 检查紧凑手机视口与当前参考视口，覆盖加载、空、错误、禁用和完成状态并保存截图。
- 用全新安装和上一正式版升级各验证一次；确认 Room schema、令牌迁移和 WorkManager 不丢任务。
- 通过 Gateway 创建作品、编辑同一实体制造冲突、解决后再次同步；断开设备后旧令牌必须失效。
