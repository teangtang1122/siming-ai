# 墨枢 / Moshu

墨枢是一个本地运行的长篇小说 AI 工作台。它把章节正文、大纲、角色、关系网、世界观、写作偏好、技能提示词和项目记忆放在同一个工作流里，让 AI 不只是“临时写一段”，而是能围绕一部作品持续工作。

## 解决什么痛点

当前大模型直接写长篇小说时，经常会遇到这些问题：

- 上下文一长就忘记前文设定，写着写着把世界观规则、伏笔、时间线写乱。
- 角色容易 OOC，尤其是人物关系、当前位置、身体状态、境界、当前目标变化后，模型很难长期记住。
- 已完本小说、长篇同人资料、几十万字设定集很难一次性塞进上下文。
- 用户手动整理角色卡、大纲、世界观很耗时，后续还要反复提醒 AI。
- 写作质量要求高时，需要先查资料、设计剧情、角色对戏、生成正文、检查问题、保存更新，这些步骤很容易漏。
- 禁用句式、文风偏好、技巧提示词散落在聊天记录里，模型未必每次都会遵守。

墨枢的目标是把这些内容沉淀成可检索、可更新、可追踪的项目资料，让 AI 在写作时自动读取相关信息，而不是每次从零开始。

## 项目怎么解决

### 作品建档

可以导入已有小说章节，按章节逐步建档。系统会提取并更新：

- 章节摘要和对应大纲节点
- 角色档案、别名、关系、出场记录
- 角色当前状态，如位置、境界、身体状况、心理状态、当前目标、冲突和装备
- 世界观条目和时间线
- 角色/世界观版本历史，方便追踪某次更新来自哪一章

这适合把已经完本的小说导入后快速初始化项目资料，也适合为同人创作、续写、番外和改写做资料准备。

### RAG 与上下文选择

当世界观、角色和章节摘要数量很多时，系统不会简单取前几条，而是通过 RAG 检索和预算打包选择本次写作最相关的资料。写章节时会优先读取：

- 当前大纲节点及附近上下文
- 最近章节摘要
- 相关角色档案和关系
- 相关世界观设定
- 项目记忆和写作偏好

前端可以看到上下文概览，知道 AI 为什么选中这些资料。

### Plan Agent

写作助手支持计划式执行。用户说“帮我写第 154 章”时，系统会把任务拆成多个步骤，而不是只做一次模型调用。

质量模式下的章节工作流包括：

1. 预览写作上下文
2. 设计剧情
3. 角色扮演或多角色对戏
4. 生成正文
5. 评估章节质量
6. 保存章节
7. 检测角色变化
8. 检测新增世界观

如果前端开启质量模式，即使用户没有在消息里写“高质量”，章节 Plan 也会走质量路径。失败步骤可以重试或从失败处继续，避免整轮对话重来。

### 技能与记忆

技能管理允许用户配置可复用的 AI 行为模板，比如：

- 某种文风审校
- 禁用句式检查
- 特定题材的写作规则
- 角色口吻控制
- 资料搜索和整理方式

技能可以设置触发词、适用范围、优先级和推荐工具。系统会在对话时匹配相关技能，并把技能提示词注入到本次任务中。

项目记忆会保存用户偏好、写作风格、工作流偏好和项目事实。质量模式下，Plan Agent 也会读取相关记忆。

### 模型供应商

系统设置中可以配置多个模型供应商。目前内置支持 OpenAI、Anthropic Claude、DeepSeek、通义千问和 Google Gemini。

如果新的供应商提供 OpenAI-compatible API，可以在系统设置中选择“自定义 OpenAI 兼容”，填写：

- 提供商标识，如 `openrouter`、`siliconflow`、`moonshot`
- API Key
- 默认模型名
- API 端点，如 `https://api.example.com/v1`

自定义供应商会使用 OpenAI-compatible 调用方式，因此适合接入兼容 `/v1/chat/completions` 和 `/v1/models` 的服务。

### 风格与禁用句式

项目可以配置：

- 叙事视角
- 文风偏好
- 自定义风格提示词
- 禁用句式
- 修辞限制
- 短句偏好

章节保存前会自动尝试修复项目配置的禁用句式，降低“AI 味”和重复模板句式。

## 可以用来做什么

- 导入完本小说进行建档，生成角色、大纲和世界观资料库。
- 基于原作资料写同人小说、番外、后续卷、if 线。
- 管理原创长篇小说，从大纲规划到章节写作再到设定维护。
- 让 AI 根据已有角色状态和世界观规则判断剧情是否合理。
- 生成新章节后自动更新角色变化和新增设定。
- 做拆书分析、资料整理、角色关系管理和世界观维护。
- 给不同作品配置不同的技能提示词和写作偏好。

## 外部 Agent 快速接入

墨枢可以作为 MCP Server 接入 Claude Code / Codex。接入后，外部 Agent 可以读取所有作品、获取墨枢写作 Prompt Pack、执行无 API 写作流程、创建新小说、写入章节和更新角色/世界观。

推荐先使用 `project_management` 权限包。它可以管理作品、写章节、创建新小说、管理技能和导出数据，但不会暴露 API Key、模型密钥等敏感配置，也不会暴露危险删除/合并工具。

### 自动配置

推荐先运行自动配置脚本。脚本会自动检测本机是否安装 Claude Code / Codex，自动寻找同目录、下载目录、桌面、`release` 目录或 `%LOCALAPPDATA%` 下的 `Moshu.exe`，找不到 exe 时会回退到源码里的 `scripts\moshu-mcp-server.py`。

如果你下载的是 GitHub Release，请把 `setup-external-agent-mcp.ps1` 放在 `Moshu.exe` 旁边，然后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-external-agent-mcp.ps1
```

如果你从源码运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-external-agent-mcp.ps1 -PreferSource
```

想先预览将要写入什么配置，可以加 `-DryRun`：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-external-agent-mcp.ps1 -DryRun
```

常用参数：

```powershell
# 只配置 Claude Code
powershell -ExecutionPolicy Bypass -File .\setup-external-agent-mcp.ps1 -Client claude

# 只配置 Codex
powershell -ExecutionPolicy Bypass -File .\setup-external-agent-mcp.ps1 -Client codex

# 手动指定 exe 路径
powershell -ExecutionPolicy Bypass -File .\setup-external-agent-mcp.ps1 -MoshuExe "C:\path\to\Moshu.exe"
```

配置成功后重启 Claude Code / Codex，然后让它调用：

```text
list_projects
```

如果能列出墨枢里的作品，就说明接入成功。

### 手动配置

如果自动脚本不适合你的环境，可以手动配置。下面示例里的路径都需要换成你自己电脑上的实际路径：

- 源码方式：把 `C:\path\to\agent` 换成墨枢源码目录。
- exe 方式：把 `C:\path\to\Moshu.exe` 换成你下载或安装的 `Moshu.exe` 完整路径。

### Claude Code

Claude Code 推荐用命令添加 MCP Server，不需要手动找配置文件：

```powershell
claude mcp add -s user moshu -- python "C:\path\to\agent\scripts\moshu-mcp-server.py" --permission-pack project_management
```

如果使用打包后的 exe：

```powershell
claude mcp add -s user moshu -- "C:\path\to\Moshu.exe" --mcp-server --permission-pack project_management
```

验证：

```powershell
claude mcp list
```

### Codex

Codex 修改配置文件：

```text
%USERPROFILE%\.codex\config.toml
```

源码方式添加：

```toml
[mcp_servers.moshu]
type = "stdio"
command = "python"
args = ["C:\\path\\to\\agent\\scripts\\moshu-mcp-server.py", "--permission-pack", "project_management"]
```

exe 方式添加：

```toml
[mcp_servers.moshu]
type = "stdio"
command = "C:\\path\\to\\Moshu.exe"
args = ["--mcp-server", "--permission-pack", "project_management"]
```

改完后重启 Codex。连接成功后，在 Claude Code / Codex 中让它调用：

```text
list_projects
```

如果能列出墨枢里的作品，就说明接入成功。

### 不绑定单个作品

默认不要加 `--project-id`，这样外部 Agent 可以通过 `list_projects` 查看所有作品，并在调用具体工具时传入 `project_id`。如果只想暴露某一个作品，再添加：

```text
--project-id YOUR_PROJECT_ID
```

### 无 API 写作模式

如果墨枢没有配置模型 API，外部 Agent 不要调用 `chapter_writer` / `evaluate_chapter` 这类内部模型工具。应使用：

- `get_prompt_pack`
- `get_quality_rubric`
- `prepare_external_writing_context`
- `save_external_chapter_draft`
- `record_external_quality_review`
- `create_chapter`
- `apply_external_story_updates`

完整说明见 [Claude Code / Codex MCP Client Setup Guide](docs/mcp/claude-code-codex-client.md)。

## 开发环境依赖

如果只是运行已经打包好的 `Moshu.exe`，不需要安装下面这些开发依赖。

如果要从源码启动、测试或打包，需要先安装：

- Git，用于拉取代码和发布版本。
- Python 3.11 或更高版本。Windows 安装 Python 时建议勾选 `py launcher`。
- Node.js 20 LTS 或更高版本，内含 npm。
- PowerShell，用于运行本项目的 Windows 启动和打包脚本。

可以先检查本机是否已经安装：

```powershell
git --version
py --version
python --version
node --version
npm --version
```

如果 `python --version` 不可用，但 `py --version` 可用，下面所有 `python` 命令都可以改成 `py`，或直接使用虚拟环境里的 `.\.venv\Scripts\python.exe`。

后端直接依赖写在 `backend\requirements.txt` 中，首次运行会安装：

- `fastapi`
- `uvicorn[standard]`
- `sqlalchemy`
- `alembic`
- `pydantic`
- `pydantic-settings`
- `cryptography`
- `python-multipart`
- `aiohttp`
- `httpx`
- `openai`
- `anthropic`
- `python-dotenv`
- `python-docx`
- `ddgs`

前端直接依赖写在 `frontend\package.json` 中，首次运行会安装：

- 运行依赖：`react`、`react-dom`、`react-router-dom`、`antd`、`@ant-design/icons`、`axios`、`zustand`、`@tiptap/react`、`@tiptap/starter-kit`、`@tiptap/extension-placeholder`、`vis-data`、`vis-network`
- 开发和测试依赖：`vite`、`typescript`、`vitest`、`jsdom`、`@vitejs/plugin-react`、`@testing-library/react`、`@testing-library/jest-dom`、`@testing-library/user-event`、`@types/react`、`@types/react-dom`

## 本地开发

首次运行需要先安装后端依赖：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果默认 PyPI 下载很慢，可以临时使用国内镜像：

```powershell
.\.venv\Scripts\python.exe -m pip install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt
```

启动后端：

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

前端建议新开一个终端，在项目根目录运行。首次运行需要先安装前端依赖：

```powershell
cd frontend
npm install
```

启动前端：

```powershell
npm run dev
```

## 参与贡献

欢迎通过 Issue 和 Pull Request 参与改进。建议先从 `main` 拉取最新代码，再为每个改动创建独立分支：

```powershell
git checkout main
git pull
git checkout -b feature/your-change
```

贡献前请先按「开发环境依赖」和「本地开发」完成本地启动。改动时尽量保持范围清晰：

- Bug 修复请说明复现步骤、原因和修复方式。
- 新功能请说明使用场景、主要交互和涉及的后端/前端模块。
- 文案、提示词、模型适配和数据迁移类改动，请在 PR 中写清楚影响范围。
- 不要提交 `.env`、API Key、本地数据库、`.crypto_key`、`.venv`、`node_modules`、`frontend\dist`、`.build`、`release`、`artifacts` 等本地文件或构建产物。

提交前建议至少运行相关检查：

```powershell
# 后端测试。pytest 当前是测试工具依赖，如本机未安装，请先在后端虚拟环境中安装。
cd backend
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest
```

前端构建和测试建议新开终端，在项目根目录运行：

```powershell
cd frontend
npm run build
npm exec vitest -- run
```

如果只改了 README 或发布文档，可以在 PR 中说明未运行代码测试的原因。

## 打包 Windows 可执行程序

在项目根目录运行：

```powershell
.\build-exe.bat
```

生成文件：

```text
release\Moshu.exe
release\NovelWritingAgent.exe
release\update.json
release\sha256.txt
```

`Moshu.exe` 是正式分发文件。`NovelWritingAgent.exe` 是旧品牌兼容别名，用于让已经安装旧版本的用户继续自动更新。

普通用户只需要运行 exe，不需要安装 Git、Python、Node.js 或 npm。

## 自动更新

exe 启动时会自动检查 GitHub 最新 Release。默认更新仓库：

```text
teangtang1122/NovelWritingAgent
```

发布新版本时，GitHub Release 中应包含：

```text
Moshu.exe
NovelWritingAgent.exe
sha256.txt
update.json
```

如果检测到 Release 版本号高于本地版本，程序会下载新的 exe，退出当前进程后自动替换并重启。

可选环境变量：

```text
MOSHU_DISABLE_UPDATE=1
MOSHU_UPDATE_REPO=owner/repo
MOSHU_UPDATE_MANIFEST_URL=https://example.com/update.json
MOSHU_GITHUB_TOKEN=...
```

旧环境变量 `NOVEL_AGENT_DISABLE_UPDATE`、`NOVEL_AGENT_UPDATE_REPO`、`NOVEL_AGENT_UPDATE_MANIFEST_URL`、`NOVEL_AGENT_GITHUB_TOKEN` 仍然兼容。

## 旧数据兼容

新版本默认使用：

```text
%LOCALAPPDATA%\Moshu
```

如果用户机器上已经有旧版数据：

```text
%LOCALAPPDATA%\NovelWritingAgent\novel_agent.db
```

并且新目录还没有有效数据库，启动器会自动继续使用旧数据目录，保证旧 exe 产生的数据可以被新版本直接读取。

## MCP Server

墨枢内置 MCP (Model Context Protocol) Server，允许外部 AI 客户端（如 Claude Desktop、Cursor）直接读取项目数据。

### 从源码运行

```bash
python scripts/moshu-mcp-server.py --permission-pack project_management
```

### MCP 客户端配置

在 Claude Desktop 的 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "moshu": {
      "command": "python",
      "args": ["scripts/moshu-mcp-server.py", "--permission-pack", "project_management"],
      "cwd": "D:\\AI\\agent"
    }
  }
}
```

或使用打包后的 exe：

```json
{
  "mcpServers": {
    "moshu": {
      "command": "C:\\path\\to\\Moshu.exe",
      "args": ["--mcp-server", "--permission-pack", "project_management"]
    }
  }
}
```

### 功能

- **只读模式**（默认）：暴露查询和分析工具，不修改项目数据
- **资源**：通过 `moshu://` URI 访问项目、章节、角色、世界观、大纲
- **提示词**：提供 `moshu_writing_context`、`moshu_continuity_check`、`moshu_fanfic_draft` 等结构化提示词

详细文档见 `docs/mcp/spec.md` 和 `docs/mcp/security.md`。

### Claude Code / Codex 集成

墨枢支持通过 MCP 让 Claude Code 或 Codex 直接操作项目数据，并在 Web UI 中实时显示外部 Agent 的工作进度。详见 `docs/mcp/claude-code-codex-client.md`。

### 无 API 写作模式

Claude Code / Codex 可以在墨枢**没有配置模型 API** 的情况下写作。墨枢提供上下文、提示词包、存储和遥测，外部模型负责生成和评审。详见 `docs/mcp/claude-code-codex-client.md` 的 "No Moshu API Mode" 章节。

## 发布到 GitHub

确保已经安装并登录 GitHub CLI：

```powershell
gh auth login
```

打包并发布：

```powershell
.\build-exe.bat
.\scripts\publish-github.ps1 -Tag v1.3.9
```

发布脚本会提交当前改动、推送到 `main`，创建或更新 GitHub Release，并上传 exe、sha256 和 update manifest。
