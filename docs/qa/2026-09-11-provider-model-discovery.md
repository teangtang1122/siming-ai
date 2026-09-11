# 模型自动发现与 DeepSeek V4.1 接入回归

## 问题与处理

| 入口 | 原有问题 | 当前行为 |
| --- | --- | --- |
| DeepSeek API | 后端、前端、配置保存及适配器使用固定 V4 白名单，新 ID 被过滤或拒绝；空结果被换回旧列表 | 删除型号白名单；服务商返回的新 ID 可发现、保存、选择，并按原 ID 请求；保留结构校验与连接验证 |
| Gemini API | 空列表被前端替换成内置型号 | 空结果如实显示，可重试获取或明确手填服务商支持的 ID |
| OpenAI、DeepSeek、Gemini、通义千问、自定义兼容 API | 通用发现服务只保留排序后的前 100 项 | 保留服务商此次响应中的全部型号，继续按 ID 去重 |
| Claude 原生 API | 只读第一页，另外截断至 100 项 | 按 `has_more` / `last_id` / `after_id` 读完分页；保留名称与容量字段；重复或缺失游标、后续页失败、总耗时超过 20 秒时明确失败，避免返回不完整列表冒充成功 |
| Android 独立模式 | 型号发现没有相同白名单，但容量表尚无 `deepseek-flash` | 新增官方容量资料；独立发现、配置序列化恢复、普通与 Agent 流式调用均保留模型 ID |

所有 API 配置现在显示模型发现状态。服务商返回空列表或请求失败时，界面说明原因并提供重新获取、手填入口。已有配置可继续编辑；不会把内置型号当成接口刚返回的可用型号。

模型 ID 仍受非空、长度和类型校验；任务模型仍校验已配置提供商及其模型目录。移除的是发行版本白名单。未知型号不因为未收录在容量表而被拒绝，容量继续使用服务商元数据、作者配置或现有默认规则。未知 DeepSeek 型号不再一律套用 384K 输出，使用通用 16K 输出默认值。

CLI 检查了动态发现、配置中的模型合并及实际调用路径，没有发现按 DeepSeek 发行版本拦截新 ID 的逻辑。本轮保持 CLI 默认模型及当前配置的处理方式。

## 官方资料

- [DeepSeek V4.1 Flash 发布说明](https://deepseek.com/news/deepseek-v4-1-flash/)：2026-09-10 发布，当前 ID 是 `deepseek-flash`。旧 Flash ID 的服务端路由由 DeepSeek 管理，司命不重写这些官方 ID。
- [DeepSeek 模型参数](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：截至 2026-09-11，`deepseek-flash` 为 1M 上下文、最大 384K 输出。后端、前端与 Android 容量资料同步更新为 `deepseek_model_docs_2026_09_11`；代理端点不套用官方容量结论。
- [Claude Models API](https://platform.claude.com/docs/en/api/models/list)：默认一页 20 项，通过游标继续读取。

## 验证结果

- 后端 202 项通过：模型发现、配置 CRUD/全局选择/实际适配器入口、普通与工具流式调用、容量目录及现有思考协议回归。
- 后端补充 56 项通过：旧配置身份迁移、上下文编排、模型就绪状态。
- 前端 38 项通过：新型号选择与保存、空列表与错误恢复、空白 ID 拒绝、模型选择及容量资料。
- Android 35 项通过：`DirectApiClientTest` 25 项、`MobileKnownModelCapacityTest` 10 项。包含 111 个型号的发现、配置序列化恢复、六种新 ID 的普通调用，以及 `deepseek-flash` 的 Agent 工具流式请求。
- 前端生产构建、最终 TypeScript 检查、后端架构/导入/Ruff 检查、前端边界/重复代码检查、生成 OpenAPI 一致性检查通过；PC/手机契约检查通过（27 项能力）。

测试使用模拟服务商响应及本地 HTTP 测试服务器，没有调用作者配置的付费模型，也没有读写作者作品。上述结果证明模型发现和请求传递链路，不代表已完成各家真实模型质量或已安装客户端的界面验收。

Android 独立模式沿用产品现有 OpenAI 兼容协议；Claude 原生 API 分页修复属于 PC 后端的原生 Claude 入口，本轮未新增 Android 原生 Anthropic 协议。

Windows Android 验证使用 JDK 17、SDK 35。此机器的 Java 本地通信需要将 `jdk.net.unixdomain.tmpdir` 指向工作区已有的短路径 `.build/uds`；这是测试进程参数，不写入应用配置。
