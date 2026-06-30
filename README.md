# 司命 / Siming

长篇小说的命运织机。

司命是一个本地运行的长篇小说 AI 工作台。它把章节、大纲、角色状态、关系网、世界观、伏笔、写作偏好和 AI 工作流放进同一个项目，让模型在几十万字之后仍能找到该看的资料、记住角色当前状态，并把生成结果真正保存回作品。

仓库名：`siming-ai`

## 当前重点

- 项目品牌已更名为 `司命 / Siming`，正式发布产物为 `Siming.exe`。
- GitHub 更新仓库默认指向 `teangtang1122/siming-ai`。
- `Moshu.exe` 和 `NovelWritingAgent.exe` 仅作为旧版本自动更新、旧数据目录和旧用户快捷方式的兼容别名保留。
- 新的外部 Agent / MCP 自动配置默认写入 `siming` 服务器条目；旧的 `moshu` 条目会被迁移或清理。
- 为了不破坏旧项目和旧客户端，部分内部协议名仍保留兼容 ID，例如 `moshu://`、`get_moshu_usage_guide`、`moshu_task_type`。

## 杀毒软件误报

Windows 杀毒软件如果把打包版识别为木马，最常见原因不是业务代码本身，而是未签名的 PyInstaller/内嵌 Python 运行时具备这些特征：

- 单文件 exe 会在临时目录解包 Python 运行时。
- 程序会启动本地 Web 服务和浏览器页面。
- 程序可能启动 Claude Code、Codex、OpenCode 等本机 CLI。
- 用户选择本地模型时，程序会启动 llama.cpp 或训练/推理相关进程。

司命不会把模型权重打包进 exe，也不会主动上传用户作品数据。正式分发前建议对发布包做代码签名、固定 Release 资产来源，并在说明里提示用户从官方 GitHub Release 下载。

## 3 分钟上手

### 1. 下载并启动

从 GitHub Release 下载 `Siming.exe`，双击运行即可。普通用户不需要安装 Python、Node.js，也不需要手动配置 MCP。

首次启动时选择小说数据目录。默认目录为：

```text
%LOCALAPPDATA%\Siming
```

如果检测到旧版目录 `%LOCALAPPDATA%\Moshu` 或 `%LOCALAPPDATA%\NovelWritingAgent`，司命会自动兼容读取，不会主动删除旧数据。

### 2. 选择 AI

在系统设置里选择一种模型来源：

- 云端 API：OpenAI、Anthropic Claude、DeepSeek、Google Gemini、通义千问或 OpenAI 兼容接口。
- 本机 CLI：Claude Code、Codex、OpenCode、MiMo Code、Cursor Agent、Kilo Code、Qwen Code、Hermes Agent 或 OpenClaw。
- 本地模型：由司命下载并管理的 GGUF 模型和 llama.cpp 运行时。

选择本机 CLI 后，司命会自动检测程序、合并 MCP 配置，并默认写入名为 `siming` 的 MCP 条目。

### 3. 创建或导入作品

你可以：

- 从零创建一本新小说。
- 上传已有 TXT/DOCX 小说并按章节导入。
- 对完本小说运行作品建档，为续写、同人、番外或改写建立资料库。
- 创建空白作品后手动写作。

### 4. 开始工作

进入作品后可以直接说：

- “根据当前大纲写第 151 章。”
- “检查这一章是否和角色状态、世界观冲突。”
- “规划接下来十章，先给我确认方向。”
- “把这 150 章逐章建档。”
- “更新主角当前伤势、位置和目标。”

运行过程会显示模型、工具调用、任务阶段、失败原因和可重试入口。

## 核心能力

### 作品建档

建档按章节推进，每章会提取并更新：

- 章节摘要和大纲节点。
- 角色档案、别名、关系和出场记录。
- 角色当前位置、年龄/时间变化、境界、身体状态、心理状态、目标、冲突和装备。
- 世界观条目、规则证据和时间线。
- 角色与世界观的版本历史。

事实抽取和候选写入分离。失败时保留已完成阶段，可从失败章节继续，不需要整轮重跑。

### 写作与 Plan Agent

质量模式会按计划执行：

1. 检索大纲、近期摘要、角色、关系、世界观、记忆和技能。
2. 设计剧情。
3. 进行角色扮演或多角色对戏。
4. 生成正文。
5. 评估章节质量与冲突。
6. 保存章节。
7. 更新角色变化。
8. 更新新增世界观。

失败步骤可以单独重试，也可以从失败步骤继续。

### RAG 与上下文

当作品有数百条世界观、角色和章节摘要时，司命会按当前任务混合检索和预算打包，优先选择当前大纲节点、近期章节、相关角色状态、冲突规则、用户偏好和项目记忆。

### 外部 Agent / No API 流程

外部 Agent 默认走不消耗司命内部模型额度的 API-free 流程：

- 只有用户明确要求使用司命内部 API 时，才调用 `internal_llm` 工具。
- 中文小说始终用中文保存角色名、别名、章节标题、摘要、大纲、事实和世界观。
- 读取优先使用本地作品文件镜像。
- 写入、删除、更新和验证必须通过 MCP 工具完成。
- 建档先读取 `cataloging_external_no_api` Prompt Pack。
- 写作先准备上下文、保存草稿、记录评审，再创建章节和更新故事状态。

常用只读工具：

```text
list_projects
get_project_files_info
list_project_files
read_project_file
search_project_files
get_prompt_pack
get_moshu_usage_guide
```

常用写入工具：

```text
create_project
create_chapter / update_chapter
create_character / update_character
create_outline_node / update_outline_node
create_worldbuilding_entry / update_worldbuilding_entry
save_external_chapter_draft
apply_external_story_updates
```

## 开发

### 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 前端

```powershell
cd frontend
npm install
npm run dev
```

### 测试

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest

cd ..\frontend
npm run build
```

## 打包与发布

```powershell
.\build-exe.bat
```

生成：

```text
release\Siming.exe
release\Moshu.exe
release\NovelWritingAgent.exe
release\update.json
release\sha256.txt
```

发布：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\publish-github.ps1 -Tag vX.Y.Z
```

## MCP Server

源码运行：

```powershell
python scripts\moshu-mcp-server.py --permission-pack auto
```

打包程序运行：

```powershell
Siming.exe --mcp-server --permission-pack auto
```

新配置示例：

```json
{
  "mcpServers": {
    "siming": {
      "command": "C:\\path\\to\\Siming.exe",
      "args": ["--mcp-server", "--permission-pack", "project_management"],
      "env": {}
    }
  }
}
```

默认不绑定单一作品，外部 Agent 可先调用 `list_projects`，再为具体工具传入 `project_id`。

## 旧数据兼容

司命保留旧品牌数据目录、旧环境变量和旧可执行文件名兼容。升级后会自动识别旧数据库、迁移运行时字段并刷新文件镜像。迁移完成前不会主动删除旧数据；确认新版本可以正常读取后，再由迁移流程清理已废弃副本。

优先使用的新环境变量：

```text
SIMING_HOME
SIMING_MODEL_ROOT
SIMING_CONTENT_ROOT
SIMING_UPDATE_REPO
SIMING_UPDATE_MANIFEST_URL
SIMING_DISABLE_UPDATE
SIMING_GITHUB_TOKEN
```

旧的 `MOSHU_*` 和 `NOVEL_AGENT_*` 变量仍会被读取。

## 参与贡献

欢迎通过 Issue 和 Pull Request 改进司命。提交前请至少运行相关后端测试和前端构建，并说明用户遇到的问题、复现步骤、修改后的行为、数据兼容/提示词/模型适配风险，以及已执行的验证。
