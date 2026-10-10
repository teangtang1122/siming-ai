# 快速开始改为 API 接入

## 当前流程

- 无可用模型时，PC 与 Docker Gateway 的快速开始均仅提供 API 接入，步骤为获取 Key、配置并测试、开始创作。
- 页面提供 DeepSeek、通义千问、OpenAI、Anthropic Claude、Google Gemini 的官网密钥管理链接，在新窗口打开。入口分别按 [DeepSeek 文档](https://api-docs.deepseek.com/)、[百炼文档](https://help.aliyun.com/zh/model-studio/get-api-key)、[OpenAI 帮助](https://help.openai.com/en/articles/4936850-where-do-i-find-my-openai-api-key)、[Claude 文档](https://platform.claude.com/docs/en/api/overview)、[Gemini 文档](https://ai.google.dev/gemini-api/docs/api-key)核对；登录后创建密钥由用户完成。
- “配置 API”打开现有模型设置中的同一配置表单，仅筛选 API 提供商；保存后使用现有“测试并启用”。返回快速开始重新读取验证结果。删除临时加入的 CLI 快速开始参数分支。
- 本地模型与已有 CLI 从模型中心、普通模型设置进入。已有已验证且启用的模型直接进入创意输入，显示实际模型并传入立项请求。
- 状态接口只读数据库，不扫描 CLI、不请求模型目录、不安装或测试模型。刷新与首次打开使用同一接口。
- 删除旧 OpenCode 免费安装、激活、登录、重试接口与后台工作器。旧任务由 Alembic `300a39_retire_free_onboarding` 一次性停止并禁用重试，历史记录、已有模型配置及作品保留。
- README 与嵌入桌面的快速开始、作品库、立项页和助手缺模型提示同步更新。

## 验证

- 后端：快速开始、退休迁移、模型可用性、数据库启动、迁移演练等 40 项通过；另外两组历史迁移测试 4 项通过。
- 本次 API 界面收敛后重跑快速开始与模型设置，共 28 项通过：覆盖官网链接与新窗口属性、仅 API 配置、已有模型、刷新后就绪、真实模型 ID 传入立项、失败重试和稍后设置。首轮后端结果如上，本次只修改其迁移提示文字。
- `npm run build` 通过（含 TypeScript 检查）；仍有既有的大分块提示。首轮架构检查 errors=0。
- `npm run screenshots:readme -- --quick-start-only` 使用真实前端和虚构数据生成并检查 1440×900 截图。
- 所有数据库验证使用测试库，没有迁移本机正在使用的小说数据库。

### 3.5.1 发布前回归

- 前端 lint 与 451 项单元测试通过；后端质量检查与 369 项测试通过。
- 架构、重复代码和生成 API 类型检查通过。
- 浏览器回归按 CI 配置执行，28 项通过、6 项按既有条件跳过。同步删除旧免费安装流程的测试断言，验证 API 官网入口、配置表单跳转、已有模型可用性与故事输入；修正快速开始页小字的颜色对比度。
- Windows 3.5.0 测试安装包已在本机完成覆盖安装与健康检查；3.5.1 正式发布包由 Release Gate 对版本、签名和安装启动结果单独核验。

## 手机端

检查了 `MainViewModel.configureDirectApi`、手机 API 配置界面和 `GatewayPairingInstrumentedTest.standaloneApiSetupIsReachableWithoutGateway`：手机独立配置 API，执行真实对话测试后保存；没有调用 PC 快速开始或已删除的 OpenCode 激活接口。本次改动的是 PC/Web 快速开始，Android 仍使用自己的通用 API 配置界面，未增加具名服务商官网入口，未修改 Android 业务代码，也未重新运行真机全流程测试。入口呈现差异已写入两端 README。
