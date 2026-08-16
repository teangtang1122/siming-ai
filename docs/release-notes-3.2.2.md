# 司命 3.2.2

3.2.2 将 Windows 正式分发从历史单 EXE 改为标准安装包，重点改善安装路径、桌面快捷方式、卸载与后续安装版更新体验。

## 主要改进

- Windows 正式版改为 `Siming-Setup.exe` 安装包，安装时可以选择程序目录。
- 桌面快捷方式作为安装选项提供并默认勾选，同时创建开始菜单入口和卸载信息。
- 安装包内部使用 PyInstaller onedir 布局，减少单文件自解包带来的运行时路径复杂度，并为后续更新维护提供稳定安装目录。
- 安装版更新优先下载并验证新的 `Siming-Setup.exe`，通过 SHA-256 与 Windows Authenticode 校验后在当前安装目录覆盖更新并重启。
- 正式 Windows Release 不再提供独立 `Siming.exe`、`update.json` 和旧 `sha256.txt`，防止新用户误下载历史便携包。
- Release Gate 增加真实 Windows 安装测试、禁止遗留单 EXE 资产检查和安装包 Artifact 保存。

## 发布资产

- `Siming-Setup.exe`：Windows 10 x64 或更高版本安装包。
- `Siming-Setup.sha256`：Windows 安装包完整性校验文件。
- `Siming.apk`、`Siming-apk-sha256.txt`：Android 客户端及校验文件。
- Gateway 容器：随 Release 构建并发布 `amd64` 与 `arm64` 镜像。

已有历史单 EXE 用户如需升级到安装版，请直接下载并运行 `Siming-Setup.exe`；程序数据与安装目录分离，既有作品和配置不会因为安装版覆盖而被删除。

如果 Windows 代码签名证书未配置，本版本 Windows 安装包只能作为官方 Release 的手动下载资产；应用内安全更新仍不会放宽可信签名要求。
