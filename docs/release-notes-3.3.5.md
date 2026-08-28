# 司命 3.3.5

3.3.5 是对话式立项上下文治理修复版本，重点解决同人文或群像作品角色很多时，立项工具把全阶段正文、完整角色集合和重复生成结果反复送入模型，导致上下文膨胀、剧情误拼接和生成不稳定的问题。

## 按需检索，不再全量展开

- `get_creation_snapshot` 与立项会话/阶段列表现在只返回 revision、状态、锁数量、顶层结构和集合计数，不再返回所有阶段正文。
- 角色、关系、地点、势力、分卷、章节和场景统一通过 `list_creation_entities` 按 artifact、类型和模型给出的 query 围栏检索；默认分页只返回 20 条摘要，精确资料再按实体 ID 读取。
- 对象化 artifact 的读取只返回标量字段和集合计数，避免一次读取角色阶段就把整个同人角色库带进 tool round。
- 立项历史只回放用户消息与最终答复，不再把旧 tool call 和 tool result 带入后续回合；超大单次工具结果也会保持为有效、受限的 JSON，而不是截断成损坏文本。

## 先读后写与增量生成

- 原生工具调用强制把读取和写入决策拆到两个模型步骤：模型必须先看到真实读取结果，下一步才能提交写入；同一步并列的写调用会被确定性拒绝且不计入失败重试。
- `generate_creation_artifact`、`refine_creation_artifact` 和 `regenerate_creation_artifact` 支持显式 `context_entity_ids` 与 `context_artifacts`。生成器只接收目标实体、作者约束、明确选中的创意及这些引用。
- 实体级生成只返回目标增量；未召回的角色和关系由运行时原样保留，不要求模型重建整个集合。
- 已有对象集合不会再次注入整阶段生成提示，也不会被全量模型输出覆盖。上下文清单仍保留审计与预算职责，但不再重复注入生成器已经显式渲染的资料。
- 没有明确选择创意方向时，阶段生成不会再默认采用第一套方案；结构 baseline 只提供字段形状和作者明确填写的标量，不再预造“故事起点”“局势推进”等剧情事实。

## 跨端与验证

- PC、Gateway、直接 MCP/CLI 和 Android 独立模式继续使用同一套生成工具契约；Android 内置的 PC 契约已重新导出。
- 新增大角色集、实体检索分页、显式引用、未召回对象保留、同模型步骤读写隔离、超大工具结果和未选择创意等回归测试。

## 下载

正式 Release 提供：

- `Siming-Setup.exe`：Windows 10 x64 或更高版本安装包
- `Siming-Setup.sha256`：Windows 安装包 SHA-256
- `Siming.apk`：Android 8.0 或更高版本正式签名安装包
- `Siming-apk-sha256.txt`：Android APK SHA-256

Windows 项目当前尚未配置 Authenticode 证书，安装前请同时核对 Release 提供的 SHA-256；Android APK 使用正式发布密钥签名。
