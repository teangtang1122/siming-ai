# Siming Android

Android 客户端位于 `mobile/android`，最低 Android 8.0（API 26），目标 API 35。它保存可写离线副本，可直接连接用户选择的 OpenAI 兼容 API，也可连接用户自有 Gateway 做跨设备同步；不包含本地模型、OpenCode、CLI、MCP 或训练运行时。

## AI 与 PC 一致性契约

- 连接 Gateway 后，作品、章节、大纲、角色和世界观的在线增删改直接调用与 PC 前端相同的 `/api/v1/projects/...` 路由，不另建“手机版业务接口”。离线时才写入 outbox，恢复连接后按修订协议同步。
- 同时配置 Gateway 与手机 API Key 时，每轮可选“PC 已配置线路”或“手机私有 Key”。两种线路都由 PC 的工作区助手、提示词、工具执行器和落库服务完成；区别只在本轮模型凭据来自哪里。
- 手机 Key 线路使用二维码内签名的 Gateway X25519 公钥进行临时加密。Gateway 只在该请求的异步上下文中解密使用，不写入模型配置表、作品副本、同步队列或日志；旧配对升级后需重新扫码才能取得加密公钥。
- 没有 Gateway 时，Android 直接调用手机保存的 API。`scripts/export-mobile-prompt-contract.py` 会从 PC `PromptSpec`、写作规则和工具注册表生成 `pc_workspace_prompt_contract.json`；手机运行同样的函数调用循环和章节/角色/大纲/世界观二级生成器，本地工具写入手机副本与 outbox。
- “AI 立项”复用 PC `creation.novel.stage@3.0.0` 的预设、动态采访、8 阶段 PromptSpec、影响依赖、JSON 修复与数据契约。连接 Gateway 时，PC 线路和手机 Key 线路都调用原生 `/api/v1/novel-creation/...` 流程；无 Gateway 时才在 Android 上执行构建生成的同源契约和 PC 对齐的确定性基线/归一化。
- 核心立项资料通过最终审阅后即可“建立正式作品档案”，前三章细纲与 PC 一样可以建档前确认，也可以稍后完善。Gateway 路线调用 PC `/apply`；纯手机路线按相同实体字段建立作品、角色关系、世界设定关系、卷纲以及已确认的章节/场景细纲，并进入离线同步队列。
- 外部小说导入与 PC 共用 TXT / Markdown / DOCX 格式边界和 20 MiB 上限。连接 Gateway 时上传原文件给 PC 权威导入服务；离线或手机独立模式在本机安全解码文本或提取 DOCX 正文，再按与 PC 一致的章节标题识别规则原子建档，不会用错误编码替换字符强行导入。
- 司命项目包使用独立 `.siming-project` 入口和磁盘流式处理，不经过小说 `ByteArray` 解析器。离线导入保留完整原包与幂等请求键；联网时先上传项目包，再回放该作品的普通 outbox。
- `backend/tests/test_mobile_prompt_contract.py` 会重建并比较该资产。PC 提示词或工具 schema 改动后若没有重新导出，测试会失败，防止手机悄悄退化成简化提示词。

## 开发构建

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
- 分别验证三条 AI 线路：Gateway + PC 模型、Gateway + 手机 Key、无 Gateway + 手机 Key；同一输入应得到相同工具参数结构和实体字段。
- 检查紧凑手机视口与当前参考视口，覆盖加载、空、错误、禁用和完成状态并保存截图。
- 用全新安装和上一正式版升级各验证一次；确认 Room schema、令牌迁移和 WorkManager 不丢任务。
- 通过 Gateway 创建作品、编辑同一实体制造冲突、解决后再次同步；断开设备后旧令牌必须失效。
