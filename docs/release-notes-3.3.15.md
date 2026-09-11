# 司命 3.3.15

3.3.15 修复模型自动发现不完整、新型号被旧列表拒绝的问题，并同步 DeepSeek V4.1 Flash 的容量资料。

## 新型号发现与配置

- DeepSeek 模型列表、配置保存和实际调用不再受固定 V4 型号白名单限制。服务商返回的新 ID 可以选择、保存并按原 ID 发起请求。
- 补充 `deepseek-flash` 的官方容量资料：1M 上下文、最大 384K 输出，PC 与 Android 同步更新。服务商仍支持的旧 Flash ID 保持原样发送。
- 未知 DeepSeek 型号不再一律套用 384K 输出默认值，使用通用 16K 默认值，可按服务商资料调整。模型 ID 的非空、类型和长度校验继续生效。

## 其他提供商

- OpenAI、DeepSeek、Gemini、通义千问及自定义兼容接口不再只保留模型列表的前 100 项。
- PC 后端原生 Claude 接口会读取全部分页；后续页失败、超时或游标异常时明确报错，避免把不完整列表当作成功结果。
- 模型发现显示加载、成功、空列表和错误状态；空列表或失败时可以重新获取或手动填写型号。DeepSeek、Gemini 不再把旧内置列表冒充接口返回结果。

## 手机端

Android 独立模式同步新增型号的容量资料，继续保留服务商返回的模型 ID。手机沿用现有 OpenAI 兼容协议；本轮原生 Claude 分页修复属于 PC/Gateway 后端，未新增 Android 原生 Anthropic 协议。

## 验证与下载

发布前相关 331 项测试通过；Windows 测试成品已验证启动、模型配置保存、111 个型号完整返回、Claude 分页及新 ID 请求传递。测试使用本地模拟接口，不代表所有真实服务商连接均已逐一验证。正式附件由标签发布流水线构建，并执行安装冒烟、签名及发布后下载校验。

- `Siming-Setup.exe`：Windows 10 x64 或更高版本安装包。
- `Siming-Setup.sha256`：Windows 安装包校验值。
- `Siming.apk`：Android 8.0 或更高版本正式签名安装包。
- `Siming-apk-sha256.txt`：Android 安装包校验值。

正式 APK 使用现有发布证书，应用 ID 为 `com.siming.mobile`。此前 `com.siming.mobile.debug` 测试版使用独立数据空间。
