# Windows 可执行程序打包

## 生成 exe

在项目根目录运行：

```bat
build-exe.bat
```

生成结果：

```text
release\Siming.exe
release\update.json
release\sha256.txt
```

`Siming.exe` 是唯一正式分发文件。旧品牌数据目录仍然兼容，但旧 exe 名不再生成、不再上传。

## 给普通用户运行

新用户发送 `release\Siming.exe` 即可。用户双击后会：

1. 自动启动本地后端服务。
2. 自动打开浏览器页面。
3. 使用本机数据目录保存数据库、密钥、模型和运行时配置。

默认数据目录：

```text
%LOCALAPPDATA%\Siming
```

如果用户已经用旧版产生过数据，新版启动时会自动检测：

```text
%LOCALAPPDATA%\Moshu
%LOCALAPPDATA%\NovelWritingAgent
```

旧目录存在且新目录没有有效数据库时，司命会继续使用旧目录，避免用户丢数据。

## 重新指定数据目录

优先使用：

```bat
set SIMING_HOME=D:\SimingData
release\Siming.exe
```

旧变量 `MOSHU_HOME`、`NOVEL_AGENT_HOME` 仍然兼容。

## 打包机要求

只有负责打包的电脑需要安装 Python、Node.js 和 npm。普通用户运行 `Siming.exe` 不需要安装这些工具。

## 自动更新

默认更新仓库：

```text
teangtang1122/siming-ai
```

发布新版本时，在 GitHub Release 上传：

```text
Siming.exe
sha256.txt
update.json
```

`sha256.txt` 只包含：

```text
<sha256>  Siming.exe
```

更新器只下载 `Siming.exe`。Release 中不要上传旧 exe 名资产。

可用环境变量覆盖更新源：

```bat
set SIMING_UPDATE_REPO=owner/repo
set SIMING_UPDATE_MANIFEST_URL=https://example.com/update.json
set SIMING_DISABLE_UPDATE=1
```

旧变量 `MOSHU_UPDATE_REPO`、`MOSHU_UPDATE_MANIFEST_URL`、`MOSHU_DISABLE_UPDATE`、`NOVEL_AGENT_*` 仍然兼容。

## MCP Server

打包后的 exe 包含 MCP Server 入口。推荐让程序自动检测和配置本机 Agent；手动排障时可以运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-external-agent-mcp.ps1
```

配置示例：

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

如果从源码运行：

```bat
python scripts\moshu-mcp-server.py --permission-pack project_management
```

入口脚本文件名暂时保留 `moshu-mcp-server.py`，用于兼容旧文档和旧配置；客户端里的服务器条目应使用 `siming`。

## Smoke Test

打包后运行：

```powershell
.\scripts\smoke-test-release.ps1
```

测试会验证 `Siming.exe`、MCP 配置脚本、服务启动和核心 API。
