# 模型请求参数与推理兼容性

核对日期：2026-11-04。规则以真实服务端点、协议和精确模型 ID 为准；中转站的同名模型不自动视为官方模型。PC 的权威请求策略在 `backend/app/ai/request_policy.py`，Android 读取由其生成的目录。

## 官方契约核对

| 服务 | 已确认差异 | 文档 |
| --- | --- | --- |
| OpenAI | GPT-6 启用推理时需省略采样参数。Luna/Sol 的 Chat Completions 工具调用仅接受 `reasoning_effort=none`；Astra、6.1 Sol、6.1 Terra 工具调用要求 Responses。 | [GPT-6 参数兼容性](https://developers.openai.com/api/docs/guides/latest-model#update-api-and-model-parameters) |
| DeepSeek | Chat 使用 `thinking.type` 控制思考，Responses 使用 `reasoning.effort`；思考时温度无效，`top_p` 行为还依赖具体模型。工具历史中的推理内容须按契约回传。 | [思考模式](https://api-docs.deepseek.com/guides/thinking_mode/) |
| 阿里云 Qwen | 混合思考模型使用 `enable_thinking`；部分模型为始终思考模式。具体字段和支持情况依模型及接口而异。 | [深度思考](https://help.aliyun.com/zh/model-studio/deep-thinking) |
| 硅基流动 | 兼容接口提供 `enable_thinking`、思考预算及采样参数，支持情况依所部署模型而异。 | [Chat API](https://docs.siliconflow.cn/docs/api/chat-completions-post) |
| Gemini | OpenAI 兼容接口把 `reasoning_effort` 映射到 Gemini 推理级别或预算；部分型号不能关闭思考。 | [OpenAI 兼容](https://ai.google.dev/gemini-api/docs/openai) |
| Anthropic | 新 Claude 型号限制非默认采样参数；旧型号的限制随思考模式变化。原生 Messages 协议与 OpenAI 兼容协议有不同字段。当前 SDK 使用 `2023-06-01` API 版本；官方说明从 `2026-10-21` API 版本起所有型号均不能发送采样参数，升级 SDK/版本头时须同步复核。 | [思考与兼容性](https://platform.claude.com/docs/en/build-with-claude/thinking) |

## 两端的高级请求参数

PC：设置 → AI 模型 → 新增/编辑 API → 选择模型 → 高级请求参数。

手机：设置 → API 管理 → 新增/编辑 API → 选择模型 → 高级请求参数。

每个 API 分别保存按模型 ID 索引的参数；切换默认模型、任务模型或刷新模型列表不会把另一模型的参数带入。PC 使用独立 JSON 配置列，已有数据库由 Alembic 迁移，旧记录默认为自动适配；手机随 API 配置一起加密保存，旧载荷缺少此字段时也默认为自动。

- `{}`：使用自动适配和业务默认值。
- `{"temperature": null}`：明确省略温度参数，使用提供商默认行为。
- OpenAI Responses：`{"reasoning":{"effort":"none"},"temperature":0.8}`。
- OpenAI Chat Completions：`{"reasoning_effort":"none","temperature":0.8}`。
- 阿里云/硅基流动的适用模型可按文档设置 `{"enable_thinking":false,"temperature":0.8}`。
- DeepSeek Chat 的适用模型可按文档设置 `{"thinking":{"type":"disabled"},"temperature":0.8}`。

高级参数只允许采样和推理控制字段；模型、消息、工具、密钥、输出额度不能在此覆盖。已确认不兼容的自定义设置会明确失败，不自动降低推理档位；未知模型或中转接口的额外限制仍以服务端实际返回为准。自动适配不意味着这些模型在所有入口和协议下都支持同样能力，手机现有直连架构仍使用 OpenAI 兼容协议。

连接测试使用同一参数策略，包含推理消耗的输出额度受配置及容量限制。输出截断最多恢复一次；参数错误、模型不存在不会被当作协议错误反复切换。

内部运行信息（`moshu_*`、`local_cli_*` 及 `storage_target`）只供司命处理，不得进入任何外部模型请求。PC 与手机使用同一权威过滤规则，生成、结构修正、流式和非流式请求均适用。

## 网络连接与模型等待

PC 的 API 执行、连接测试和模型列表读取共用模型 HTTP 客户端策略，默认建连等待 20 秒，可通过 `MODEL_CONNECT_TIMEOUT_SECONDS` 配置（1–120 秒）。该值用于 TCP、代理和 TLS 建连，独立于回合时限、流式空闲等待及 SDK 的读写时限；修改它不会降低推理设置或增加请求重试。连接超时保留结构化 `timeout_phase=connect` 和实际连接时限，PC 立项、项目助手及 API 错误不再将其描述为模型生成慢。

手机独立直连原有建连时限为 20 秒，保持不变；接收响应、请求总时限与流式空闲时限分别控制。Android 的 `BEFORE_RESPONSE` 表示尚未收到响应，不能据此进一步断言 TCP 或 TLS 失败；PC 可从 HTTPX 异常链区分建连阶段。这是网络库提供的诊断精度差异，不改变超时后停止业务执行的契约。

## 建连中断的有界恢复

PC/API 在网关内处理、手机直连在自身 HTTP 执行器内处理：TCP、DNS、代理或 TLS 建连失败且确认请求尚未发往模型时，最多恢复一次，即连续失败时总共两次连接尝试；预算耗尽后结束本次调用，不再叠加常规重试或切换协议、路径。SDK 的隐藏重试关闭。用户取消与请求等待时限仍优先生效。

PC 结合 HTTPX 的 `ConnectError`/`ConnectTimeout` 异常链与原站发送事件确认尚未送达，代理 CONNECT 不算原站请求，任何已发送请求或已收到响应（包括重定向）都会关闭新增恢复边界；未知阶段不进入新增恢复；手机用每次请求的 OkHttp 事件确认尚未开始发送原站请求头，收到响应也立即关闭该恢复边界。新增恢复不处理发送中断、已收到模型事件、结构/引用错误或 HTTP 参数/授权错误；已有的显式流式检查点续传和输出恢复继续遵守原边界。

连接异常按网络故障记录，立项、项目助手和 API 保留确定的失败阶段，不再把 `Connection error` 显示为未知的“处理失败”。恢复只重取未送达的模型请求，不重放已执行的业务工具，也不修改历史或已确认阶段资料。

## 运行追踪字段的边界

`moshu_operation_id`、`moshu_storage_target` 是司命的内部执行元数据。作品助手、立项和本机 CLI 都使用这一命名空间；CLI 适配器从内部上下文取得编号，再关联进程监控、取消与恢复。业务模型和工具结果中的正式 `operation_id` 字段保持原契约，不受请求元数据过滤影响。

所有外部模型请求统一剥离 `moshu_*`、`local_cli_*`，并防止保留字段 `operation_id`、`storage_target` 进入供应商请求顶层。过滤不修改消息或工具参数中的同名业务字段。未知参数类 HTTP 400 明确记录为 `request_parameters`，诊断保留状态码、参数名和错误码，不公开参数值，也不把接口拒绝误报成未知模型故障。手机直连读取同一生成策略。
