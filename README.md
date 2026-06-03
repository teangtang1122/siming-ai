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

## 本地开发

后端：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
$env:PYTHONPATH='.'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

前端：

```powershell
cd frontend
npm install
npm run dev
```

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

## 发布到 GitHub

确保已经安装并登录 GitHub CLI：

```powershell
gh auth login
```

打包并发布：

```powershell
.\build-exe.bat
.\scripts\publish-github.ps1 -Tag v1.3.6
```

发布脚本会提交当前改动、推送到 `main`，创建或更新 GitHub Release，并上传 exe、sha256 和 update manifest。
