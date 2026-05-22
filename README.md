# 墨枢 / Moshu

墨枢是一个本地运行的小说写作 AI 工作台，包含作品管理、章节写作、大纲规划、角色管理、世界观、拆书分析和统一项目助手。

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
```

`Moshu.exe` 是正式分发文件。`NovelWritingAgent.exe` 是旧品牌兼容别名，用于让已经安装旧版本的用户继续自动更新。

## 自动更新

exe 启动时会自动检查 GitHub 最新 Release。默认更新仓库仍使用：

```text
teangtang1122/NovelWritingAgent
```

发布新版本时，在 GitHub Release 中上传：

```text
Moshu.exe
NovelWritingAgent.exe
sha256.txt
update.json
```

如果检测到 Release 版本号高于本地版本，程序会下载新的 exe，退出当前进程后自动替换并重启。普通用户不需要安装 git、Python、Node.js 或 npm。

可选环境变量：

```text
MOSHU_DISABLE_UPDATE=1
MOSHU_UPDATE_REPO=owner/repo
MOSHU_UPDATE_MANIFEST_URL=https://example.com/update.json
MOSHU_GITHUB_TOKEN=...
```

旧环境变量 `NOVEL_AGENT_DISABLE_UPDATE`、`NOVEL_AGENT_UPDATE_REPO`、`NOVEL_AGENT_UPDATE_MANIFEST_URL`、`NOVEL_AGENT_GITHUB_TOKEN` 仍然兼容。`MOSHU_UPDATE_MANIFEST_URL` 的优先级高于 GitHub Release API，适合私有分发或自建更新清单。

## 旧数据兼容

新版本默认使用 `%LOCALAPPDATA%\Moshu`。如果用户机器上已有旧版 `%LOCALAPPDATA%\NovelWritingAgent\novel_agent.db`，且新目录还没有有效数据库，启动器会自动继续使用旧数据目录，保证旧 exe 产生的数据可以被新版本直接读取。
