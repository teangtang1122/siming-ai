# Windows 可执行程序打包

## 生成 exe

在项目根目录运行：

```bat
build-exe.bat
```

生成结果：

```text
release\Moshu.exe
release\NovelWritingAgent.exe
release\update.json
```

这个命令会先构建前端静态文件，再把后端、依赖和前端页面一起打包进一个 Windows 可执行文件。

`Moshu.exe` 是正式分发文件。`NovelWritingAgent.exe` 是兼容旧品牌自动更新的别名，内容与 `Moshu.exe` 相同。

## 给普通用户运行

新用户发送 `release\Moshu.exe` 即可。用户双击后会：

1. 自动启动本地后端服务。
2. 自动打开浏览器页面。
3. 使用本机数据目录保存数据库和密钥。

默认数据目录：

```text
%LOCALAPPDATA%\Moshu
```

如果用户已经用旧版 `NovelWritingAgent.exe` 产生过数据，新版启动时会自动检测：

```text
%LOCALAPPDATA%\NovelWritingAgent\novel_agent.db
```

当旧数据库存在且新目录没有有效数据库时，新版会继续使用旧目录，避免用户丢数据。

目录中会保存：

```text
novel_agent.db
.crypto_key
```

## 重新指定数据目录

如果需要把数据放到指定位置，可以在启动前设置环境变量：

```bat
set MOSHU_HOME=D:\MoshuData
release\Moshu.exe
```

旧变量 `NOVEL_AGENT_HOME` 仍然兼容。

## 打包机要求

只有负责打包的电脑需要安装 Python、Node.js 和 npm。普通用户运行 `Moshu.exe` 不需要安装这些工具。

## 自动更新

打包后的 exe 会在启动时检查 GitHub 最新 Release。默认更新仓库为：

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

`sha256.txt` 可以直接填写打包生成的 SHA256 值。为了兼容旧版本，文件中应同时包含：

```text
<sha256>  Moshu.exe
<sha256>  NovelWritingAgent.exe
```

用户启动旧版本时，旧更新器会下载 `NovelWritingAgent.exe`；用户启动新版本时，新更新器优先下载 `Moshu.exe`，找不到时会回退到旧名资产。

可以用环境变量覆盖更新源：

```bat
set MOSHU_UPDATE_REPO=owner/repo
set MOSHU_UPDATE_MANIFEST_URL=https://example.com/update.json
set MOSHU_DISABLE_UPDATE=1
```

旧变量 `NOVEL_AGENT_UPDATE_REPO`、`NOVEL_AGENT_UPDATE_MANIFEST_URL`、`NOVEL_AGENT_DISABLE_UPDATE` 仍然兼容。

如果单文件 exe 启动较慢，也可以生成目录版：

```bat
build-exe.bat -OneDir
```

## MCP Server

打包后的 exe 包含 MCP Server 入口。MCP 客户端（如 Claude Desktop、Cursor）可通过 stdio 方式连接：

```json
{
  "mcpServers": {
    "moshu": {
      "command": "C:\\path\\to\\Moshu.exe",
      "args": ["--mcp-server", "--permission-pack", "project_management"],
      "env": {}
    }
  }
}
```

如果从源码运行：

```bat
python scripts/moshu-mcp-server.py --permission-pack project_management
```

MCP Server 默认为只读模式，仅暴露查询和分析工具。
