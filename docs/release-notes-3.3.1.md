# 司命 3.3.1

3.3.1 是一次应用身份与发布质量升级：Windows 主程序补齐可信的版本资源，PC 与 Android 均新增完整的“关于我们”页面，手机端图标与 PC 端统一，同时固定 Windows 构建工具链以提升发布可复现性。

## 主要更新

### Windows 应用身份与构建可靠性

- 为 `Siming.exe` 嵌入公司名、产品名、文件说明、原始文件名、文件版本和产品版本等 Windows 版本资源。
- 构建后自动读取并校验 PE 版本信息，避免发布空白、错误或与应用版本不一致的元数据。
- 固定 Python、pip、setuptools、PyInstaller、Node.js、npm 与 Inno Setup 版本，并校验 Windows 构建依赖锁，降低不同环境打包结果漂移。
- 保留安装包、签名、SHA-256 校验与安装冒烟测试组成的正式发布门禁。

### PC 关于我们

- 新增独立“关于我们”页面，集中展示产品定位、当前版本、维护者、开源许可证、数据边界与联系方式。
- 提供项目主页、版本发布和问题反馈入口，帮助用户确认安装来源与版本信息。

### Android 关于我们与统一图标

- 手机设置页新增“关于我们”入口和独立页面，展示与 PC 端一致的产品、维护者、Apache 2.0 许可证、数据边界及项目链接。
- 页面版本号直接读取 Android 构建版本，避免展示信息与安装包不一致。
- Android 顶栏图标和应用启动图标改用 PC 端司命图标，并补齐自适应、圆形及 Android 13 单色图标资源。
- 保留手机端 API 密钥本机加密、作品本地优先和官方不转存正文的边界说明。

## 下载

正式 Release 提供：

- `Siming-Setup.exe`：Windows 安装包
- `Siming-Setup.sha256`：Windows 安装包 SHA-256
- `Siming.apk`：Android 安装包
- `Siming-apk-sha256.txt`：Android APK SHA-256

Windows 正式发布继续使用安装包形式，不提供旧版单文件 `Siming.exe`。
