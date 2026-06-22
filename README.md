# 墨枢 / Moshu

墨枢是一个本地运行的长篇小说 AI 工作台。它把章节、大纲、角色状态、关系网、世界观、伏笔、写作偏好和 AI 工作流放进同一个项目，让模型在几十万字之后仍能找到该看的资料、记住角色当前状态，并把生成结果真正保存回作品。

## 它解决什么痛点

直接用通用大模型写长篇小说，常见问题并不只是“文笔不够好”：

- 上下文越长，越容易忘记前文设定、时间线、伏笔和力量规则。
- 角色容易 OOC，尤其会忘记当前位置、年龄、境界、伤势、目标、关系变化和已有装备。
- 完本小说或几十万字资料无法一次塞进上下文，手动整理角色卡和世界观又很耗时。
- 大纲、正文、角色状态和世界观分别维护，写完一章后经常忘记同步更新。
- 不同模型、Claude Code、Codex、OpenCode 等工具各有一套调用方式，换入口后工作流和质量容易变化。
- 禁用句式、文风偏好和创作技巧散落在聊天记录里，模型未必持续遵守。

墨枢通过作品建档、RAG 上下文选择、角色/世界观时间线、Plan Agent、技能提示词、项目记忆和统一工具链解决这些问题。数据库是唯一权威写入源，本地 Markdown/JSON 镜像供模型快速阅读；所有修改通过墨枢写回，前端、索引、版本历史和缓存保持一致。

## 普通用户 3 分钟上手

### 1. 下载并启动

从 GitHub Release 下载 `Moshu.exe`，双击运行即可。普通用户不需要安装 Python、Node.js，也不需要手动运行 MCP 配置脚本。

首次启动时选择一个小说数据目录。没有特殊需求时直接使用默认目录即可；如果检测到旧版数据，墨枢会自动迁移并保留兼容。

### 2. 选择 AI

打开系统设置，任选一种方式：

- 配置 DeepSeek、OpenAI、Claude、Gemini、通义千问或兼容 OpenAI API 的服务。
- 选择本机已经安装的 Claude Code、Codex、OpenCode、MiMo Code、Cursor Agent、Kilo Code、Qwen Code、Hermes Agent 或 OpenClaw。

选择本机 CLI 后，墨枢会自动检测程序、合并 MCP 配置并设置适合无人值守运行的权限。它只维护名为 `moshu` 的 MCP 条目，不会覆盖用户已有的其他 MCP Server。

助手设置中的模型选择会覆盖全局默认模型，并在桌面助手与作品内助手之间保持。全局默认模型只在用户没有单独选择时使用。

### 3. 创建或导入作品

你可以：

- 对助手说：“帮我创建一本克苏鲁+规则怪谈、能写 1000 章的小说。”
- 在创建作品时上传已有小说文本。
- 创建空白作品后手动写作。
- 导入完本小说并运行作品建档，为续写、同人、番外或改写建立资料库。

### 4. 开始工作

进入作品后，可以直接说：

- “根据当前大纲写第 151 章。”
- “检查这一章是否和角色状态、世界观冲突。”
- “规划接下来十章，先给我确认方向。”
- “把这 150 章逐章建档。”
- “更新主角当前伤势、位置和目标。”

运行过程会显示模型正在读取什么、调用什么工具、哪一步失败。支持失败步骤重试和从失败处继续，不需要整轮重来。

## 可以用来做什么

- 从零创建小说：生成书名、核心卖点、主角、角色关系、世界规则、卷纲和前三章。
- 导入完本小说建档：逐章建立摘要、大纲、角色、关系、世界观和时间线。
- 基于原作资料写同人、番外、续集、if 线或改写版本。
- 管理原创长篇：从立项、大纲、正文到角色状态和设定维护。
- 让 AI 判断剧情是否合理、角色是否会行动、设定是否冲突。
- 使用快速模式直接产出，或使用质量模式进行剧情设计、角色对戏、评估和更新。
- 通过 Claude Code、Codex 等外部 Agent 直接读取小说文件镜像，并用 MCP 工具安全写回。

## 核心工作流

### 作品建档

建档按章节推进，每章会提取并更新：

- 章节摘要和对应大纲节点
- 角色档案、别名、关系和出场记录
- 角色当前位置、年龄/时间变化、境界、身体状态、心理状态、目标、冲突和装备
- 世界观条目、规则证据和时间线
- 角色与世界观版本历史，记录由哪一章触发、发生了什么变化

事实提取和候选写入分离。失败时保留已完成阶段，只重试失败阶段；候选按章节顺序写入，避免长篇角色背景被乱序合并。

### 写作与 Plan Agent

快速模式以较少检索和检查完成任务。质量模式会按计划执行：

1. 检索大纲、近期摘要、角色、关系、世界观、记忆和技能
2. 设计剧情
3. 进行角色扮演或多角色对戏
4. 生成正文
5. 评估章节质量与冲突
6. 保存章节
7. 更新角色变化
8. 更新新增世界观

失败步骤可单独重试，也可以从失败步骤继续。

### RAG 与上下文

当作品有数百条世界观、角色和章节摘要时，墨枢不会固定取前几十条。系统会根据当前任务进行混合检索和预算打包，优先选择：

- 当前大纲节点及附近节点
- 最近章节摘要和相关详细章节
- 涉及角色、别名、关系和状态
- 与本章冲突相关的世界规则
- 用户偏好、项目记忆和匹配到的技能

前端可查看上下文概览，知道 AI 选中了哪些资料以及为什么。

### 技能与记忆

技能是可复用的 AI 行为模板，可以定义文风审校、题材规则、角色口吻、禁用句式、资料整理方法和推荐工具。项目助手和外部 Agent 可读取同一套公开 Prompt Pack 与技能规则。

项目记忆保存用户偏好、写作风格、工作流偏好和长期项目事实。记忆可管理、可检索，不依赖某一次聊天是否还在上下文里。

## 本机 CLI 与外部 Agent

### 自动配置

Moshu 启动时会扫描受支持的本机 Agent CLI。保存本机 CLI 供应商时也会立即重新检测和配置：

- Claude Code
- Codex
- OpenCode
- MiMo Code
- Cursor Agent
- Trae
- Kilo Code
- Qwen Code
- Hermes Agent
- OpenClaw

默认采用易用性优先的可信本地模式，自动允许读取小说数据目录和调用 Moshu MCP 工具。用户无需把配置文件和 `Moshu.exe` 一起下载，也无需手工修改 Claude/Codex 配置。

在长任务中，本机 CLI 会在小说数据目录读取 UTF-8 任务文件和只读作品镜像，而不是把整章正文塞进 Windows 命令行。写入、修改和删除仍通过 Moshu MCP 完成。

### 外部 Agent 默认规则

外部 Agent 默认走不消耗墨枢内部模型额度的 API-free 流程：

- 只有用户明确要求使用墨枢内部 API 时，才调用 `internal_llm` 工具。
- 中文小说始终用中文保存角色名、别名、章节标题、摘要、大纲、事实和世界观。
- 读取优先使用本地作品文件镜像；写入必须使用 MCP 工具。
- 建档先读取 `cataloging_external_no_api` Prompt Pack。
- 写作先读取外部写作 Prompt Pack、准备上下文、保存草稿、记录评审，再创建章节和更新故事状态。

常用只读工具：

- `list_projects`
- `get_project_files_info`
- `list_project_files`
- `read_project_file`
- `search_project_files`
- `get_prompt_pack`
- `get_moshu_usage_guide`

常用写入工具：

- `create_project`
- `create_chapter` / `update_chapter`
- `create_character` / `update_character`
- `create_outline_node` / `update_outline_node`
- `create_worldbuilding_entry` / `update_worldbuilding_entry`
- `save_external_chapter_draft`
- `apply_external_story_updates`

### 自动配置失败时

普通用户应先在墨枢的“外部 Agent / MCP”界面点击重新检测。源码仓库中的 `scripts/setup-external-agent-mcp.ps1` 只作为高级排障工具，不作为 GitHub Release 资产分发：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-external-agent-mcp.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File .\scripts\setup-external-agent-mcp.ps1
```

配置成功后重启对应 Agent，让它调用 `list_projects` 即可验证。

## 数据与架构

### 权威数据与文件镜像

- SQLite/数据库：唯一权威写入源，保存作品、章节、角色、关系、世界观、历史、对话和任务状态。
- 本地小说目录：数据库导出的只读 Markdown/JSON 镜像，供 CLI Agent 和搜索读取。
- Redis：可选热点缓存；未配置时自动使用进程内 TTL 缓存。
- RAG 索引：用于在长篇资料中检索当前任务最相关的片段。

规范目录下的 `chapters/`、`characters/`、`worldbuilding/`、`outline/` 和 `relationships/` 不应手工修改。内容写入后，墨枢会失效缓存、更新索引并刷新镜像。

### 模型供应商

内置支持 OpenAI、Anthropic Claude、DeepSeek、通义千问、Google Gemini 和多种本机 CLI。其他服务只要兼容 OpenAI `/v1/chat/completions` 与 `/v1/models`，即可通过“自定义 OpenAI 兼容”接入。

模型标识使用 `provider:model`，例如：

```text
deepseek:deepseek-v4-flash
claude_cli:claude-code
codex_cli:codex-cli
```

## 开发者指南

### 环境

- Python 3.11+
- Node.js 20+
- Git
- PowerShell

后端：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

前端：

```powershell
cd frontend
npm install
npm run dev
```

测试：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest

cd ..\frontend
npm run build
```

### 打包

```powershell
.\build-exe.bat
```

生成：

```text
release\Moshu.exe
release\NovelWritingAgent.exe
release\update.json
release\sha256.txt
```

`NovelWritingAgent.exe` 仅用于旧品牌自动更新兼容。Release 不再包含 MCP 配置脚本，因为 MCP 自动检测和配置已经由程序完成。

### 发布

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\publish-github.ps1 -Tag vX.Y.Z
```

发布时应把同一用户功能的代码、测试、文档和版本号合并成一个发布提交，完成验证后一次推送并创建标签，避免把每个小步骤分别推到 GitHub。不要提交 `.env`、API Key、本地数据库、用户 MCP 配置、`.venv`、`node_modules`、`frontend/dist`、`.build`、`release` 或 `artifacts`。

### MCP Server

从源码启动 MCP Server：

```powershell
python scripts\moshu-mcp-server.py --permission-pack auto
```

打包程序：

```powershell
Moshu.exe --mcp-server --permission-pack auto
```

默认不绑定单一作品，外部 Agent 可先调用 `list_projects`，再为具体工具传入 `project_id`。完整协议和任务板位于 `docs/mcp/`。

## 旧数据兼容

Moshu 保留旧品牌数据目录和旧可执行文件名兼容。升级后会自动识别旧数据库、迁移运行时字段并刷新文件镜像。迁移完成前不会主动删除旧数据；确认新版本可以正常读取后，再由迁移流程清理已废弃副本。

## 参与贡献

欢迎通过 Issue 和 Pull Request 改进墨枢。提交前请至少运行相关后端测试和前端构建，并说明：

- 用户遇到的问题和复现步骤
- 修改后的行为
- 涉及的数据兼容、提示词、模型适配或迁移风险
- 已执行的验证
