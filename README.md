# 司命 / Siming

长篇小说的命运织机。

司命是一个本地运行的长篇小说 AI 工作台。它把章节、大纲、角色状态、关系网、世界观、伏笔、写作偏好和 AI 工作流放进同一个项目，让模型在几十万字之后仍能找到该看的资料、记住角色当前状态，并把生成结果真正保存回作品。

仓库名：`siming-ai`

## 当前重点

- v2.8.3 将“检测到 CLI”和“模型真实可用”彻底分开：只有完成真实对话验证的模型才会进入助手、新书和写作流程。
- OpenCode 官方登录由司命通过 Windows ConPTY 托管，登录地址与一次性凭据都在 GUI 内处理；鉴权、额度、超时和不可用状态会给出准确提示。
- 仪表盘首次任务、设置页模型分组与移动导航已统一；正式发行资产必须从版本 tag 指向的精确提交构建。
- OpenCode 激活任务和下载进度会保存在本地数据库；应用重启后可以继续，免费模型受限时会自动尝试下一个候选。
- 项目品牌已更名为 `司命 / Siming`，正式发布产物为 `Siming.exe`。
- GitHub 更新仓库默认指向 `teangtang1122/siming-ai`。
- 发布资产只保留 `Siming.exe`；不再生成或上传 `Moshu.exe`、`NovelWritingAgent.exe`。
- 新的外部 Agent / MCP 自动配置默认写入 `siming` 服务器条目；旧的 `moshu` 条目会被迁移或清理。
- 为了不破坏旧项目和旧客户端，部分内部协议名仍保留兼容 ID，例如 `moshu://`、`get_moshu_usage_guide`、`moshu_task_type`。

## 它解决什么痛点

直接用通用大模型写长篇小说，常见问题并不只是“文笔不够好”：

- 上下文越长，越容易忘记前文设定、时间线、伏笔和力量规则。
- 角色容易 OOC，尤其会忘记当前位置、年龄、境界、伤势、目标、关系变化和已有装备。
- 完本小说或几十万字资料无法一次塞进上下文，手动整理角色卡和世界观又很耗时。
- 大纲、正文、角色状态和世界观分别维护，写完一章后经常忘记同步更新。
- 不同模型、Claude Code、Codex、OpenCode 等工具各有一套调用方式，换入口后工作流和质量容易变化。
- 禁用句式、文风偏好和创作技巧散落在聊天记录里，模型未必持续遵守。

司命通过作品建档、RAG 上下文选择、角色/世界观时间线、Plan Agent、技能提示词、项目记忆和统一工具链解决这些问题。数据库是唯一权威写入源，本地 Markdown/JSON 镜像供模型快速阅读；所有修改通过司命写回，前端、索引、版本历史和缓存保持一致。

## 杀毒软件误报

Windows 杀毒软件如果把打包版识别为木马，最常见原因不是业务代码本身，而是未签名的 PyInstaller/内嵌 Python 运行时具备这些特征：

- 单文件 exe 会在临时目录解包 Python 运行时。
- 程序会启动本地 Web 服务和浏览器页面。
- 程序可能启动 Claude Code、Codex、OpenCode 等本机 CLI。
- 用户选择本地模型时，程序会启动 llama.cpp 或训练/推理相关进程。

司命不会把模型权重打包进 exe，也不会主动上传用户作品数据。正式分发前建议对发布包做代码签名、固定 Release 资产来源，并提示用户只从官方 GitHub Release 下载。

## 3 分钟上手

### 1. 下载并启动

从 GitHub Release 下载 `Siming.exe`，双击运行即可。普通用户不需要安装 Python、Node.js，也不需要手动配置 MCP。

首次启动时选择小说数据目录。默认目录为：

```text
%LOCALAPPDATA%\Siming
```

如果检测到旧版目录 `%LOCALAPPDATA%\Moshu` 或 `%LOCALAPPDATA%\NovelWritingAgent`，司命会自动兼容读取，不会主动删除旧数据。

### 2. 免费开始写小说

完全没有模型配置时，司命会自动打开“快速开始”。点击“免费开始写小说”后，司命会：

1. 从 OpenCode 官方 Release 下载约 70 MB 的 Windows CLI，并核对官方 SHA256。
2. 自动发现和测试当前可免费使用的模型，不要求你理解模型名称。
3. 测试成功后设为默认模型，并让你用一句故事想法生成三套小说创意。

不需要安装 Node.js、打开命令行或填写 API Key。免费模型、额度和数据政策由 OpenCode 或对应模型服务提供方决定，未来可能调整；请勿向免费模型提交私密或敏感内容。

已经有模型的用户仍可在系统设置里选择其他来源：

在系统设置里选择一种模型来源：

- 云端 API：OpenAI、Anthropic Claude、DeepSeek、Google Gemini、通义千问或 OpenAI 兼容接口。
- 本机 CLI：Claude Code、Codex、OpenCode、MiMo Code、Cursor Agent、Kilo Code、Qwen Code、Hermes Agent 或 OpenClaw。
- 本地模型：由司命下载并管理的 GGUF 模型和 llama.cpp 运行时。

选择本机 CLI 后，司命会自动检测程序、合并 MCP 配置，并默认写入名为 `siming` 的 MCP 条目。

维护者可通过 `SIMING_OPENCODE_MIRROR_URLS` 配置分号分隔的 HTTPS 下载镜像模板，其中 `{url}` 表示官方 URL、`{asset}` 表示官方文件名。官方源始终优先，镜像文件也必须通过官方 SHA256 校验。

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

## v2.6.1 重点

- 发布链路改为只生成、上传和校验 `Siming.exe`。
- `update.json` 和 `sha256.txt` 只包含 `Siming.exe`，更新器不再回退下载旧 exe 名。
- 旧数据目录、旧环境变量和旧 MCP 协议名仍按兼容层读取，但不再作为发布产物。

## v2.6.0 重点

- 全项目更名为 `司命 / Siming`，仓库为 `siming-ai`，宣传语为“长篇小说的命运织机”。
- 修复建档模型路由：显式模型优先，其次全局默认 API/CLI，只有明确选择本地任务模型或无全局默认时才进入本地任务模型。
- 新书生成和建档入口补齐模型传递，减少 CLI 可用却误报 `need_model` 或被本地模型抢跑的问题。
- 新书生成取消模板兜底冒充 LLM 输出；候选允许部分成功并重试失败分支。
- 建档候选解析兼容 `character_state`、缺失 `type` 但字段可推断的角色/世界观/关系/大纲候选。
- 候选写入会跳过未命名角色、空壳角色状态、无内容世界观和缺少摘要的大纲，避免污染作品档案。
- MCP/外部 Agent 默认服务器名迁移为 `siming`，并保留旧 `moshu` 配置迁移。

## 开发

后端：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

前端：

```powershell
cd frontend
npm install
npm run dev
```

常用检查：

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
cd frontend
npm run build
```

打包：

```powershell
.\build-exe.bat
```

发布：

```powershell
powershell -NoProfile -File scripts\publish-github.ps1
```

项目管理和路线图见：

- [docs/project-management.md](docs/project-management.md)
- [docs/roadmap.md](docs/roadmap.md)

## 打包产物

默认打包输出：

```text
release\Siming.exe
release\update.json
release\sha256.txt
```

`Siming.exe` 是唯一正式分发文件。旧数据目录仍会自动兼容读取，但旧 exe 名不再生成、不再上传。

## 外部 Agent MCP

手动启动 MCP 入口：

```powershell
python scripts\moshu-mcp-server.py --permission-pack auto
```

入口脚本文件名暂时保留 `moshu-mcp-server.py`，用于兼容旧文档和旧配置；客户端里的服务器条目应使用 `siming`。

## 许可证

本项目采用 Apache License 2.0，详见 [LICENSE](LICENSE)。
