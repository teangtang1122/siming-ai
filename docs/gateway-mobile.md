# Android 与用户自有 Gateway

## 设计结论

司命不建设官方小说数据服务器。跨设备能力由用户自己运行的 Gateway 提供：桌面端、NAS、家中常开主机或自有云主机保存权威数据库；Android 保存可写离线副本。司命官方发布程序、APK 和容器镜像，但不经手作品正文。

```text
Windows 桌面 ─┐
               ├─ 用户自有 Gateway（权威修订线） ─ PC 已配置模型
Android 离线副本 ┘                         └─ 手机 Key（单请求加密凭据）

Android（无 Gateway） ─ PC 同源提示词/工具循环 ─ 用户选择的 OpenAI 兼容 API
```

桌面内置 Gateway 保留桌面端全部本地能力。Docker Gateway 是 headless 运行时，只开放同步、设备管理和云端模型所需能力，不运行本地模型、OpenCode、CLI、MCP 或训练。电脑关机后，只有部署在 NAS、常开主机或云主机上的 Gateway 才能继续为手机同步和执行云端 AI 请求。

## 部署方式

### 桌面内置

1. 打开“系统设置 → 跨设备 Gateway”。
2. 启用 Gateway，确认公布地址和允许主机名，保存后重启司命。
3. 逐部点击“加入同步”。司命先备份 SQLite，再迁移并核对实体数量与摘要哈希。
4. 生成一次性二维码，用手机扫描；手机提交名称后，在桌面批准。

桌面端只在用户显式启用后监听远程连接。没有加入同步的既有作品不会被远程设备读取。

### Docker / NAS

```powershell
git clone https://github.com/teangtang1122/siming-ai.git
cd siming-ai
$env:SIMING_GATEWAY_BOOTSTRAP_KEY = "请使用至少12位随机口令"
docker compose -f compose.gateway.yml up -d
```

也可以直接使用与桌面端和 APK 相同版本的镜像，例如当前源码版本对应 `ghcr.io/teangtang1122/siming-ai-gateway:3.3.8`。生产部署应固定完整版本号，不要仅依赖 `latest`。默认映射宿主机 8000 端口，数据写入 Docker 卷 `siming-gateway-data`。首次打开管理页时输入 `SIMING_GATEWAY_BOOTSTRAP_KEY`；成功后服务器写入 12 小时 HttpOnly、SameSite=Strict 会话 Cookie，口令不会进入浏览器存储。当前同步协议仍为 v1。

建议显式设置：

```text
SIMING_GATEWAY_NAME=书房 Gateway
SIMING_GATEWAY_ADVERTISED_URL=https://siming.example.ts.net
SIMING_GATEWAY_ALLOWED_HOSTS=siming.example.ts.net,192.168.1.20
```

`SIMING_GATEWAY_ADVERTISED_URL` 不得带账号、路径或查询参数。Compose 中的管理口令为必填项。

## 连接网络

- 同一可信局域网：可使用 `http://192.168.x.x:8000`。HTTP 会明文传输授权令牌，不应在公共 Wi-Fi 使用。
- Tailscale：让 Gateway 和手机加入同一 tailnet，优先使用 MagicDNS/HTTPS；无需把端口暴露到公网。
- 自有域名：在 Caddy、Nginx 或 Traefik 后配置 HTTPS，并把域名加入允许主机列表。公网 HTTP 会被 Android 拒绝。

不要直接把 8000 端口暴露到公网。反向代理必须覆盖 TLS、访问日志脱敏、请求大小限制和安全更新。

## Android 使用

APK 支持 Android 8.0（API 26）及以上。手机端可以：

- 新建作品或导入 TXT；较长章节会拆成不超过约 20 万字符的连续章节。
- 离线编辑章节、大纲、角色、关系、世界观、伏笔与叙事治理资料。
- 连接 Gateway 时，在线增删改走与 PC 前端完全相同的作品、章节、大纲、角色、世界观和叙事治理 API；因此服务端校验、章节快照、内容同步和治理副作用保持一致。未连接或暂时断网时才进入离线 outbox。
- 在“设置”中配置 OpenAI 兼容 API，自动获取或手动填写模型后执行真实对话测试；配置成功后，无需 Gateway 或电脑开机即可使用项目助手。
- 手机直连支持 Responses API 与 Chat Completions；配置测试会记住实际可用协议。无 Gateway 时，手机加载由 PC 源码生成的完整工作区提示词、工具 schema、写作规则和四类二级生成器，实际执行查询与写入动作，不再使用简化补全文本。
- 同时存在 Gateway 和手机 Key 时，项目助手明确显示“PC 已配置线路”和“手机私有 Key”两个选择，不自动偷换。无论选哪条，Gateway 都执行同一套 PC 提示词、工具、数据结构和落库流程。
- 手机首页提供独立的“AI 立项”入口，不再把立项伪装成手工表单：先以一句创意进入动态采访，再按创意、文风与世界观、角色、地点与势力、卷纲和最终审阅逐步生成、调整与确认；前三章细纲与 PC 一样可在建档前生成，也可稍后完善。题材预设、阶段顺序、影响依赖图、提示词与 JSON 契约都来自 PC 构建资产。
- 立项连接 Gateway 时直接使用 PC 的 `/api/v1/novel-creation/...` 会话、阶段任务与 `/apply` 建档服务。选手机 Key 时，单次加密凭据驱动同一 PC 执行器；无 Gateway 时由 Android 执行同源 V3 提示词，并将正式建档结果保存为与 PC 同构的同步实体。
- 选手机 Key 时，Key 仍只长期保存在 Android Keystore。手机使用配对二维码中签名的 X25519 公钥把本轮 `base_url/api_key/model/protocol/issued_at` 加密；Gateway 解密后仅放入请求作用域，任务结束立即释放且不会覆盖 PC 模型配置。
- 查看同步状态与冲突。同步先上传本机变更再拉取服务器修订；同一资料两边都改动时保留双方版本，由用户选择。

Android 不包含本地模型、OpenCode、本机 CLI、MCP 或训练能力。API Key 和 Gateway 令牌分别由 Android Keystore 加密保存，不进入作品数据库、同步队列或日志；系统备份被禁用。手机直连地址在正式版中必须使用 HTTPS。断开设备会尽力先撤销服务器授权再清除本机 Gateway 令牌。

提示词资产不是手工维护的手机版副本。发布前必须运行 `scripts/export-mobile-prompt-contract.py`；后端漂移测试会从 PC `PromptSpec`、阶段依赖与工具注册表重新生成并逐项比较。导出资产还包含由 PC 运行时现场计算的确定性基线与归一化夹具，Android 单测会逐阶段重放并做 JSON 全等比较，覆盖概念种子、角色 profile、世界实体关系、分卷范围、开篇章节/场景 metadata 与最终审阅字段。配对记录若来自不含 `gateway_encryption_public_key` 的旧版本，需要断开并重新扫码后才能选择手机 Key 线路。

## 数据、备份与恢复

Gateway 是同步权威源，但不是唯一备份。建议：

1. 定期停止写入后备份 `/data/siming.db`、`/data/projects` 与 Gateway 签名密钥。
2. Docker 升级前备份整个卷；桌面加入同步前保留司命自动生成的数据库备份。
3. 不要复制正在写入的 SQLite 单文件作为唯一备份；应使用卷快照或 SQLite 在线备份。
4. 恢复时先停止旧 Gateway，在隔离端口验证 `/health`、作品数量和摘要哈希，再切换手机地址。

删除通过 tombstone 同步，默认保留 90 天。撤销设备会使其访问令牌和刷新令牌失效；手机丢失时应立即从管理页撤销。

## 版本与兼容

当前同步协议为 v1。桌面、Gateway 与 APK 使用同一应用版本，但同步兼容由协议版本单独判断。升级顺序建议为 Gateway → 桌面 → Android；升级后先用一部非关键作品验证配对、离线编辑、冲突处理和恢复。

安全细节见 [Gateway 威胁模型](security/gateway-threat-model.md)，APK 构建和签名见 [mobile/README](../mobile/README.md)。
