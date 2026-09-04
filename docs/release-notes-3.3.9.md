# 司命 3.3.9

3.3.9 将项目工作台与新书立项 Agent 的历史上下文改为可追溯的动态预算机制。只要作者未删除会话，完整消息和运行步骤就会继续持久保存；只有发送给模型的活动上下文会在接近真实容量时整理为结构化 checkpoint，并保留最近完整原文、最新作者消息和未消费的工具事务。

## 对话 checkpoint 与动态预算

- PC、Gateway 与 Android 独立 Agent 共用 `conversation_context_frame.v1`，不再依赖固定的最近 8 条、12 条或 6 回合裁剪。
- 每次模型调用按当前模型窗口、系统提示、工具 Schema、消息协议、输出预留和安全余量进行 token 预检；短会话不会额外调用压缩模型。
- 较早的闭合回合可以整理为持久化 checkpoint。作者原话引用绑定消息 ID、字符范围和内容哈希；工具执行账本由真实 RunStep 与持久化结果确定性生成。
- 模型生成的语义总结明确标记为非权威导航，不能覆盖当前项目数据库、最新作者意图或真实工具执行结果。
- 容量未知、当前消息自身过大或 checkpoint 生成失败时会明确停止并提供恢复提示，不会静默截断历史后继续执行。
- 工作台与立项界面可以查看 checkpoint 的触发原因、整理范围、模型、保留项和警告；原始聊天不会因整理而删除。

## 原生工具协议与写入安全

- 未闭合和刚交付的工具事务始终以原生 `assistant tool call → tool result` 结构整体保留；已消费结果只按完整事务原子回收，不会产生孤立调用或结果。
- OpenAI Responses 保留真实 `call_id` 与原始 JSON 参数；Anthropic 保留原生 thinking/provider state 和并行工具结果对应关系。
- 工具样式文本、checkpoint 内容和普通 JSON 文本都不能冒充原生工具调用；不支持安全结构化调用的模型会在执行前明确失败。
- 工具批次在执行前做整体数量与体积校验。超限批次不会调用任何处理器，也不会通过截断、伪造 ID 或部分执行隐藏失败。
- 本机 Agent CLI 无法证明 checkpoint 生成过程已隔离 shell、文件系统和 MCP 时会失败关闭；不会静默跨提供商回退。

## 跨端、安全与恢复

- 完整 transcript、稳定消息顺序、checkpoint 来源和状态进入持久化模型，并带有幂等、CAS、失效、取消和陈旧任务恢复边界。
- Gateway 提供设备作用域、哈希校验和幂等的 transcript 导入；请求体、频率、作品归属与来源范围均受服务端校验。
- Android 保留完整本地会话归档、共享 checkpoint 结构和动态容量状态；重启后的未完成回合会按失败状态恢复，不会伪装成已完成。
- 手机私有模型凭据同时携带窗口、输出、安全余量与容量可信度；精确档案优先，缺少档案时使用带 `unverified` 标记的 256K 有界兜底。
- 对外运行、步骤和 checkpoint 接口只返回允许展示的脱敏字段，模型和 SSE 不接收底层异常、凭据或内部路径。

## 兼容升级

本版本包含 3.3.8 的大纲保存、可审阅章节修订、版本冲突保护与建档对账修复。数据库会从 `300a27_chapter_revision_drafts` 继续升级，经本版对话上下文迁移链到达 `300a32_context_source_ids`。

## 下载

正式 Release 提供：

- `Siming-Setup.exe`：Windows 10 x64 或更高版本安装包
- `Siming-Setup.sha256`：Windows 安装包 SHA-256
- `Siming.apk`：Android 8.0 或更高版本正式签名安装包
- `Siming-apk-sha256.txt`：Android APK SHA-256

Windows 项目当前尚未配置 Authenticode 证书时，请在安装前同时核对 Release 提供的 SHA-256；Android APK 使用正式发布密钥签名。
