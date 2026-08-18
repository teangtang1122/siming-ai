# 司命 3.2.4

3.2.4 重点完成 Android 与 PC 写作工作区的跨端对齐，并补齐手机独立写章的可恢复、可审计闭环。

## 主要更新

### Android 与 PC 数据契约进一步统一

- 正文章节新增独立 `sort_order`，正文顺序不再被大纲节点顺序影响；PC 与 Android 统一使用权威阅读顺序。
- Android 角色、世界观、大纲、章节、伏笔与治理数据进一步收敛到 PC canonical contract，减少同步后“未命名”伪实体和字段形状漂移。
- 补齐角色关系、Character AI Config、角色版本历史、章节快照/diff/恢复、世界观版本与时间线等高级能力。
- 角色关系编辑保留有向关系语义；章节恢复、配置同步等危险操作增加防重和归属校验。

### 手机独立写章上下文对齐

- Android 独立 Agent 现在消费由 PC 权威策略导出的便携 `ContextManifest`。
- 写章前统一执行上下文预算、必选锚点、来源哈希、请求指纹和过期检测。
- 角色解析纳入大纲关联、名称、别名、关系网和叙事治理状态。
- 当目标大纲、模型、作品资料或上下文来源变化时，旧清单会被判定为 stale，而不是继续盲写。

### 写章恢复与建档闭环

- 手机独立写章草稿与 ContextManifest 可持久化恢复。
- 增加稳定写章 ID、取消状态和重复提交保护，降低断流、重试后产生重复章节的风险。
- 手机离线章节同步到 PC 后进入正式建档流程，继续产生 PC 权威快照、叙事检查点和建档状态。
- 新增跨端 E2E 回归，比较 PC canonical 路径与 Android replay 路径最终章节、快照、检查点及建档结果。

### 对齐治理与 CI

- 新增 Android / PC 机器可读能力契约及生成文档。
- Architecture CI 会检查新增手机路由、可写实体和独立 Agent 工具是否完成明确的跨端对齐决策。
- ContextManifest 策略资产加入漂移检查，避免 PC 策略升级后手机静默使用旧规则。

## 数据升级

- 启动时会自动执行章节 `sort_order` 数据库迁移，并按照升级前可见顺序回填已有章节。
- 已有作品无需手工调整大纲或重新建档。

## 下载

正式 Release 提供：

- `Siming-Setup.exe`：Windows 安装包
- `Siming-Setup.sha256`：Windows 安装包 SHA-256
- `Siming.apk`：Android 安装包
- `Siming-apk-sha256.txt`：Android APK SHA-256

Windows 正式发布不再提供旧版单文件 `Siming.exe`。