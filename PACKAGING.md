# Windows 安装与发布

## 推荐分发方式

PC 正式版只发布 **Windows 安装包**，构建入口：

```bat
build-installer.bat
```

正式 Windows 发布资产：

```text
release\Siming-Setup.exe
release\Siming-Setup.sha256
```

其中：

- `Siming-Setup.exe`：普通用户唯一应下载的 Windows 安装包。
- `Siming-Setup.sha256`：安装包完整性校验文件。

`Siming.exe` 单文件包、`update.json` 和 `sha256.txt` 不再属于正式 Release 资产。`build-installer.bat` 会在打包前主动删除 `release` 目录中的这些旧产物，Release Gate 与本地发布脚本也会拒绝或清理同名旧资产，避免新用户误下载。

**系统要求：Windows 10 x64 或更高版本。** Windows 7、Windows 8/8.1 和 32 位 Windows 不在支持范围内。

## 安装体验

安装器使用 Inno Setup，默认安装到：

```text
%LOCALAPPDATA%\Programs\Siming
```

安装向导会显示安装目录页，用户可以改到其他磁盘或目录。

安装向导还会询问是否“在桌面创建快捷方式”。该选项默认勾选；用户可以主动取消。安装器同时创建开始菜单入口和卸载信息。

安装器采用当前用户安装模式，不要求管理员权限即可安装到默认目录。用户若主动选择受保护的系统目录，则 Windows 权限规则仍然适用。

程序数据与安装目录分离。默认数据目录仍然是：

```text
%LOCALAPPDATA%\Siming
```

小说数据库、密钥、模型、日志、缓存与启动器配置不会随着覆盖安装而被删除。旧数据目录仍兼容：

```text
%LOCALAPPDATA%\Moshu
%LOCALAPPDATA%\NovelWritingAgent
```

## 安装包内部结构

安装包内部使用 PyInstaller `--onedir` 产物：

```text
<安装目录>\
├── Siming.exe
├── .siming-installed
└── _internal\...
```

这里的 `Siming.exe` 是**安装目录里的程序主入口**，不是单独提供下载的单文件发行包。

`.siming-installed` 是安装版标记，更新器用它识别正式安装布局。这样日常启动不再依赖 onefile 每次启动时的完整自解包流程，运行时文件也可以由安装器统一覆盖和维护。

## 构建机要求

负责打包的 Windows 机器需要：

- Python（带 Tk，且可用于 PyInstaller）
- Node.js / npm
- Inno Setup（`ISCC.exe`）

可以通过环境变量显式指定 Inno Setup 编译器：

```powershell
$env:SIMING_INNO_ISCC = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
.\scripts\build-installer.ps1
```

普通用户不需要安装 Python、Node.js 或 Inno Setup。

`build-exe.bat` 与 `scripts\build-exe.ps1` 仍保留给开发、排障和打包底层复用。它们不是正式 Windows Release 的发布入口；正式发布应始终使用 `build-installer.bat`。

如果只需要检查 onedir 产物，可运行：

```powershell
.\scripts\build-exe.ps1 -OneDir
```

## 应用内更新

安装版查找 GitHub Release 中的：

```text
Siming-Setup.exe
Siming-Setup.sha256
```

用户在设置页确认更新后，司命会：

1. 下载新安装包到 `%LOCALAPPDATA%\Siming\updates`。
2. 校验 SHA-256。
3. 校验 Windows Authenticode 可信签名。
4. 用户点击安装后，退出当前司命。
5. 以静默模式运行新安装包，并强制使用当前安装目录。
6. Inno Setup 覆盖程序文件并重新启动司命。

升级时会沿用之前的安装目录和附加任务选择，因此用户第一次安装时如果取消了桌面快捷方式，后续更新不会擅自重新创建。

历史单 EXE 用户不再依赖 Release 中的兼容桥自动迁移。需要迁移时，直接通知用户下载当前版本 `Siming-Setup.exe` 并运行安装向导即可。程序数据仍位于独立的 `%LOCALAPPDATA%\Siming` 数据目录，因此安装版可以继续使用既有数据。

### 更新安全要求

应用内更新只接受同时满足以下条件的 Windows 更新资产：

1. SHA-256 与发布校验值一致。
2. Windows Authenticode 签名可信。
3. 签名包含可信时间戳。

正式签名需要 GitHub Actions Secrets：

```text
SIMING_WINDOWS_CODESIGN_PFX_BASE64
SIMING_WINDOWS_CODESIGN_PASSWORD
```

证书、私钥和口令不得提交到仓库、日志或 Release 资产。

本地安装包签名：

```powershell
.\scripts\sign-windows-installer.ps1 `
  -ReleaseDir release `
  -CertificatePath C:\secure\siming-codesign.pfx `
  -CertificatePassword $env:SIMING_CODESIGN_PASSWORD
```

然后验证：

```powershell
.\scripts\verify-windows-installer.ps1 `
  -ReleaseDir release `
  -RequireTrustedSignature
```

没有 Windows 代码签名证书时，只能发布供用户主动下载的手动安装资产；应用内更新器不会降低签名要求。

## GitHub Release

正式 Release 的 Windows / Android 文件应为：

```text
Siming-Setup.exe
Siming-Setup.sha256
Siming.apk
Siming-apk-sha256.txt
```

以下 Windows 文件禁止作为新版本 Release 资产：

```text
Siming.exe
update.json
sha256.txt
```

GitHub Actions 的 Release Gate 会：

1. 构建 onedir 安装负载。
2. 编译 `Siming-Setup.exe`。
3. 确认没有遗留单 EXE Release 资产。
4. 执行后端、前端和发布契约测试。
5. 对安装包执行自定义安装目录的无人值守安装冒烟测试。
6. 有证书时签名 Windows 安装包。
7. 验证安装包 SHA、签名与 Android 资产。
8. 全部通过后只上传安装包及 Android 资产。

本地 `scripts\publish-github.ps1` 使用相同的安装包唯一分发规则；如果目标 tag 已经存在旧 `Siming.exe`、`update.json` 或 `sha256.txt`，发布脚本会先删除它们。

## 重新指定数据目录

程序数据目录和程序安装目录是两件不同的事。如果需要修改数据目录：

```bat
set SIMING_HOME=D:\SimingData
```

旧变量 `MOSHU_HOME`、`NOVEL_AGENT_HOME` 仍然兼容。

## Android APK

Android 使用独立的长期签名密钥。手动发布时使用 `-IncludeAndroid`，确保 APK 与校验文件一同上传。

本地构建机通过以下环境变量提供签名信息：

```text
SIMING_ANDROID_KEYSTORE_FILE
SIMING_ANDROID_KEYSTORE_PASSWORD
SIMING_ANDROID_KEY_ALIAS
SIMING_ANDROID_KEY_PASSWORD
ANDROID_SDK_ROOT
JAVA_HOME
```

GitHub Actions 使用 `SIMING_ANDROID_KEYSTORE_BASE64` 保存同一密钥的 Base64 内容。密钥与口令不得写入仓库、构建日志或 Release 资产。

构建和验证：

```powershell
.\scripts\build-android-release.ps1
.\scripts\verify-android-release.ps1 -ExpectedVersion 3.2.1
```

## Gateway 容器

正式版本同时发布：

```text
ghcr.io/teangtang1122/siming-ai-gateway:<version>
ghcr.io/teangtang1122/siming-ai-gateway:<major.minor>
ghcr.io/teangtang1122/siming-ai-gateway:latest
```

镜像必须包含 `linux/amd64` 与 `linux/arm64`，以 UID 10001 非 root 运行；`/data` 可写而 `/app` 不可写。

可用环境变量覆盖更新源：

```bat
set SIMING_UPDATE_REPO=owner/repo
set SIMING_UPDATE_MANIFEST_URL=https://example.com/update.json
set SIMING_DISABLE_UPDATE=1
```

旧变量 `MOSHU_UPDATE_REPO`、`MOSHU_UPDATE_MANIFEST_URL`、`MOSHU_DISABLE_UPDATE`、`NOVEL_AGENT_*` 仍然兼容。

## MCP Server

安装后的 MCP 可直接指向安装目录中的：

```text
<安装目录>\Siming.exe
```

推荐让程序自动检测和配置本机 Agent；手动排障时可以运行：

```powershell
powershell -NoProfile -File .\scripts\setup-external-agent-mcp.ps1
```

如果从源码运行：

```bat
python scripts\moshu-mcp-server.py --permission-pack project_management
```

入口脚本文件名暂时保留 `moshu-mcp-server.py`，用于兼容旧文档和旧配置；客户端里的服务器条目应使用 `siming`。
