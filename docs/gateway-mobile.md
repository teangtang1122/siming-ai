# Android 与用户自有 Gateway

## 设计结论

司命不建设官方小说数据服务器。Android 在手机独立保存作品并执行写作流程；跨设备同步可选用用户自己的桌面端、NAS 或云主机 Gateway。司命官方发布程序、APK 和容器镜像，不经手作品正文。

```text
Android ─ 本机数据库、写作/建档工具 ─ 用户选择的模型 API
   └─ 可选跨设备同步 ─ 用户自有 Gateway ─ Windows 桌面
   └─ 作者显式选择 PC 模型 ─ Gateway 远程执行
```

桌面内置 Gateway 保留桌面端全部本地能力。Docker Gateway 是 headless 运行时，只开放同步、设备管理和云端模型所需能力，不运行本地模型、OpenCode、CLI、MCP 或训练。电脑关机不影响手机本地操作及手机 API；跨设备同步和显式远程模型任务需要相应 Gateway 在线。

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

也可以直接使用与桌面端和 APK 相同版本的镜像，例如当前源码版本对应 `ghcr.io/teangtang1122/siming-ai-gateway:3.3.13`。生产部署应固定完整版本号，不要仅依赖 `latest`。默认映射宿主机 8000 端口，数据写入 Docker 卷 `siming-gateway-data`。首次打开管理页时输入 `SIMING_GATEWAY_BOOTSTRAP_KEY`；成功后服务器写入 12 小时 HttpOnly、SameSite=Strict 会话 Cookie，口令不会进入浏览器存储。当前同步协议仍为 v1。

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
- 作品、章节、大纲、角色、世界观和治理页始终在手机完成读写。Room 事务同时维护章节历史、建档失效、关联及待同步操作；保存 Gateway 地址不会把编辑切换为远程请求。
- 在“设置”中配置 OpenAI 兼容 API，自动获取或手动填写模型后执行真实对话测试；配置成功后，无需 Gateway 或电脑开机即可使用项目助手。
- 手机直连支持 Responses API 与 Chat Completions；配置测试会记住实际可用协议。无 Gateway 时，手机加载由 PC 源码生成的完整工作区提示词、工具 schema、写作规则和四类二级生成器，实际执行查询与写入动作，不再使用简化补全文本。
- 项目助手默认使用手机 API，在手机执行 PC 同源提示词、工具和数据契约。保存了 Gateway 时仍走同一路径；作者可以显式切换到需要远程主机的 PC 模型。
- 手机首页提供独立的“AI 立项”入口，不再把立项伪装成手工表单：先以一句创意进入动态采访，再按创意、文风与世界观、角色、地点与势力、卷纲和最终审阅逐步生成、调整与确认；前三章细纲与 PC 一样可在建档前生成，也可稍后完善。题材预设、阶段顺序、影响依赖图、提示词与 JSON 契约都来自 PC 构建资产。
- 手机 API 立项在 Android 完成动态采访、阶段生成、编辑确认和正式作品建档；显式 PC 模型路线使用远程会话。打开立项页不会自动请求已保存但不可达的 Gateway。
- 手机 Key 由 Android Keystore 加密保存，手机 API 请求直接发送给作者配置的模型服务，不通过 Gateway 转发。
- 查看同步状态与冲突。同步先上传本机变更再拉取服务器修订；同一资料两边都改动时保留双方版本，由用户选择。
- 配置手机 API 后，可在章节草稿页选择“保存并建档”，或在作品工具页选择“为待建档章节建档”，与 Gateway 连接状态无关。手机使用 PC 同源提示词、工具目录和候选 schema，先保存正文，再生成完整计划，校验角色、设定与大纲 ID，最后在同一个本机事务中更新摘要、角色基础信息/剧情状态/写作约束及版本、世界观、场景、大纲关联和治理资料。
- 手机可独立查看章节历史、比较并恢复版本、调整章节顺序、编辑角色关系和 AI 配置、查看世界观版本及时间线，导出 TXT、Word 或 PDF。恢复版本后会回退受影响的建档来源，并提示重新建档。
- 从作品库删除会移除当前手机副本、原包和待同步记录，不需要远程确认，其他设备的作品保留；该作品不会在后续同步时自动重新下载。
- 手机建档失败或取消时保留正文、候选和具体错误，可在作品工具页明确重试；应用退出中断的任务也会保留。成功后才解除本章的建档门槛，继续下一章仍由作者发起。
- 本机建档记录与普通编辑按顺序同步。已完成的计划交给 PC 同一建档执行器校验并写入，不重复调用模型；内容、版本或来源档案已变化时会拒绝覆盖并保留手机结果。同步这类记录需要更新 PC/Gateway，旧版没有接收接口。角色与章/场景大纲的关联也会保存在完整项目包中。

手机任务的进度、取消和重试保存在 Room；同步前不出现在 PC 任务中心。独立建档目前通过完整计划校验后整章应用，没有 PC 的逐候选手工编辑模式。手机严格拒绝非法治理引用并要求同一模型修正，连续校验失败会停止，保留正文和计划供明确重试。

Android 不包含本地模型、OpenCode、本机 CLI、MCP 或训练能力。API Key 和 Gateway 令牌分别由 Android Keystore 加密保存，不进入作品数据库、同步队列或日志；系统备份被禁用。手机直连地址在正式版中必须使用 HTTPS。断开设备会尽力先撤销服务器授权再清除本机 Gateway 令牌。

提示词资产由 `scripts/export-mobile-prompt-contract.py` 从 PC 源码生成；后端漂移测试会逐项比较 PromptSpec、阶段依赖与工具注册表。共享夹具覆盖概念种子、角色 profile、世界关系、分卷范围、开篇章节/场景及最终审阅字段。独立运行验证范围与平台差异见 [手机独立操作验收](testing/mobile-independent-authoring.md)。

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
