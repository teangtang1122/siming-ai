# 前端新建态与迟到请求覆盖修复计划

> 状态：实现与本地回归已完成；平台发布验收由 `v3.3.6` Release Gate 记录
> 审计基线：`v3.3.5` / `b3ce3a4`
> 建议发布版本：`3.3.6`
> 适用范围：PC Web/Desktop 前端；后端 API、数据库和 Android 业务契约原则上不变

## 1. 背景与结论

用户反馈在 3.3.5 中无法新增角色。代码审计确认，正式作品的角色管理页存在确定性状态竞争：

1. 作品已经至少有一个角色。
2. 作者点击角色页右上角“＋”。
3. `startCreate` 将 `selectedId` 设为 `null`，希望进入新建态。
4. `fetchCharacters` 依赖 `selectedId`，因此选中项变化会重新触发列表请求。
5. 列表请求返回后，代码把“`selectedId` 为空”解释为“尚未初始化”，自动选择第一名已有角色。
6. 新建态被旧角色详情替代，用户看到“点新增没反应”或“新增不了角色”。

这不是后端角色创建接口失败，也不是 3.3.5 立项上下文裁剪直接导致。`POST /projects/{project_id}/characters` 仍可正常创建角色；问题在于前端同时用 `selectedId = null` 表示“尚未初始化”“没有选中项”和“正在新建”，三个不同业务状态发生冲突。

完全相同的“刷新后自动选回第一项”目前只在角色页确认存在。但审计还发现多个同类异步风险：旧详情请求、旧对话请求或旧编辑器请求晚于当前操作返回时，可能覆盖作者刚进入的新建态、刚选择的对象或未保存输入。

本计划采用一个统一原则修复这些问题：

> 当前界面目标和未保存输入由最新作者操作拥有；旧请求只能完成网络请求，不能回写当前界面。

## 2. 项目约束

实施必须遵循根目录 `AGENTS.md` 和 `docs/architecture/adr-006-frontend-state-and-contracts.md`：

- 只保留一个权威业务路径，不增加备用新建入口或静默 fallback。
- 不通过关键词、路由猜测或历史选中项替代作者当前操作。
- 新建态、未保存草稿和当前编辑对象不得被迟到请求覆盖。
- 服务器状态与客户端编辑状态分离；列表刷新不能暗中改变编辑器意图。
- PC 与 Android 共用后端契约时保持输入校验、写入语义和错误语义一致。
- 修复必须包含可复现回归测试，不能只依赖人工快速点击验证。

## 3. 范围边界

### 3.1 本次范围

本次修复覆盖以下两类问题：

1. **选择状态歧义**：以 `null` 同时表示初始化、空状态和新建态。
2. **迟到响应覆盖**：异步请求完成后不校验其目标是否仍是当前目标，直接写入界面状态。

涉及页面按优先级处理：

| 优先级 | 页面/组件 | 风险 |
| --- | --- | --- |
| P0 | `CharactersPage.tsx` | 无法新增第二个角色；旧详情可能覆盖新建表单并造成误保存 |
| P0 | `GuiAssistantChat.tsx` 的立项资料编辑器 | 旧阶段请求可能覆盖当前阶段、重新打开已关闭编辑器或丢弃输入 |
| P1 | `GuiAssistantChat.tsx`、`WorkspaceAssistantChat.tsx` 的对话历史 | 旧对话请求可能覆盖“新对话”或当前已选择对话 |
| P2 | `PromptPacksPage.tsx` | 列表选中项与编辑器内容可能不一致 |
| P2 | `CatalogingPage.tsx`、`DeconstructPage.tsx` | 快速切换历史任务/报告时旧响应可能覆盖当前对象 |
| P2 | `SkillsPage.tsx`、`ContextGovernancePage.tsx` | 快速切换版本或 Manifest 详情时可能显示旧对象 |

### 3.2 不在本次范围

- 不修改角色、立项对象或对话的后端数据模型。
- 不新增角色创建 API，不更改现有角色字段契约。
- 不调整 3.3.5 的立项上下文检索和 Agent 工具分类。
- 不把全部旧页面一次性迁移到新的全局状态库。
- 不以延长防抖、增加等待时间或禁用所有切换按钮作为根治方案。
- 如果用户反馈实际入口是“新书立项对话 → 角色与关系”，应另建问题验证 Agent 工具调用链，不能混入本前端角色页缺陷。

## 4. 统一状态设计

### 4.1 编辑目标使用显式状态

角色页不再用 `selectedId = null` 承担三个含义。改为互斥状态：

```ts
type CharacterEditorTarget =
  | { mode: 'initial' }
  | { mode: 'empty' }
  | { mode: 'create' }
  | { mode: 'view'; characterId: string }
```

状态语义：

- `initial`：页面第一次加载，允许在列表返回后默认选择第一项。
- `empty`：列表为空或当前对象被删除后没有可显示对象。
- `create`：作者明确点击新增；任何列表刷新都不得改变该状态。
- `view`：查看或编辑一个明确 ID 的正式角色。

列表刷新只更新列表数据，选择策略遵循以下规则：

| 当前状态 | 列表刷新后的行为 |
| --- | --- |
| `initial` 且有角色 | 选择第一名角色，进入 `view` |
| `initial` 且无角色 | 进入 `empty` |
| `create` | 保持 `create`，不得自动选择已有角色 |
| `view` 且目标仍存在 | 保持原目标 |
| `view` 且目标已删除 | 选择明确的下一项；没有剩余项则进入 `empty` |
| `empty` | 保持空状态，除非当前操作明确要求选择新创建对象 |

### 4.2 所有详情请求使用请求代际

新增一个轻量、统一的客户端请求代际工具，供尚未迁移到 TanStack Query 的页面使用。它只解决客户端“最后一次目标拥有写回权”，不创建第二份服务器状态：

```ts
const generation = requestGate.next()
const response = await load(target)
if (!requestGate.isCurrent(generation)) return
apply(response)
```

必须支持：

- `next()`：启动新目标请求并返回代际编号。
- `invalidate()`：新建、关闭、切换上下文或卸载时立即使旧请求失效。
- `isCurrent(generation)`：响应写回前校验。

仅检查代际还不够。详情响应还必须核对请求目标与当前目标一致，例如 `sessionId + artifact`、`conversationId` 或 `characterId`。

### 4.3 保存响应也必须校验目标

读取请求和保存请求使用相同原则：

- 发起保存时冻结 `targetId`、`expectedRevision` 和请求代际。
- 保存成功后可以刷新列表缓存，但只有编辑器仍指向同一目标时，才更新当前表单和修订号。
- 关闭 A、切换到 B 后，A 的保存响应不得把编辑器切回 A。
- 失败结果只显示在仍属于该请求目标的编辑器；不得把 A 的错误挂到 B 上。

## 5. 分阶段实施

### 阶段 A：修复角色新建与详情覆盖（P0）

涉及文件：

- `frontend/src/pages/CharactersPage.tsx`
- 新增 `frontend/src/__tests__/CharactersPage.test.tsx`
- 如采用公共请求代际工具，新增到 `frontend/src/shared/` 下的单一权威位置

修改内容：

1. 用显式 `CharacterEditorTarget` 替代 `selectedId + null` 的多义状态。
2. `fetchCharacters` 不再依赖选中 ID 触发重复请求；列表加载与编辑目标分离。
3. 只有首次初始化允许默认选择第一名角色。
4. 点击新增时：
   - 先使角色详情、版本历史和 AI 配置旧请求失效；
   - 进入 `create`；
   - 清空旧角色详情、版本和 AI 配置；
   - 重置表单并设置 `role_type = supporting`、空能力列表和演进追踪默认值；
   - 不把“空白新建表单”标成已有数据已保存。
5. 选择正式角色时，用同一代际加载详情、版本和 AI 配置；每个响应写回前核对角色 ID。
6. 新建保存始终调用 `POST`；编辑正式角色始终调用对应 ID 的 `PUT`，不得根据迟到详情推断。
7. 创建成功后明确进入新角色的 `view` 状态，再刷新列表；列表刷新不得切换到其他角色。
8. 删除当前角色后，根据最新列表确定下一项或空状态，不能依赖闭包中的旧 `selectedId`。

完成标准：已有任意数量角色时，点击“＋”后新建表单持续可编辑，后台任何列表或旧详情响应都不能选回旧角色。

### 阶段 B：修复立项资料编辑器的迟到读取与保存（P0）

涉及文件：

- `frontend/src/components/GuiAssistantChat.tsx`
- `frontend/src/__tests__/GuiAssistantChat.test.tsx`

修改内容：

1. 为 `openArtifactEditor` 增加独立的详情请求代际；不能复用仅保护资料列表的 `creationArtifactRequestRef`。
2. 请求目标使用 `{ sessionId, artifact }`，响应写回前同时校验代际和目标。
3. 以下操作必须使详情请求失效：
   - 打开另一个阶段；
   - 返回资料列表；
   - 收起资料面板；
   - 切换立项会话；
   - 组件卸载。
4. 迟到详情响应不得：
   - 重新打开已经关闭的编辑器；
   - 把阶段 A 覆盖到阶段 B；
   - 把 `artifactEditorDirty` 重置为 `false`；
   - 用服务器旧值覆盖作者已经输入的内容。
5. `saveExpandedArtifact` 发起时冻结目标与 revision。保存完成后：
   - 始终可以更新对应资料的列表摘要；
   - 仅当当前编辑器仍是同一目标时更新编辑器内容和 revision；
   - 不得因 A 的保存成功把已经打开的 B 切回 A。
6. 保留现有 revision 冲突语义；不得通过静默覆盖绕过 409。

完成标准：在慢网络下快速切换、返回或关闭编辑器，界面始终服从最后一次作者操作，未保存输入不丢失。

### 阶段 C：修复对话历史覆盖“新对话”（P1）

涉及文件：

- `frontend/src/components/GuiAssistantChat.tsx`
- `frontend/src/components/WorkspaceAssistantChat.tsx`
- 对应现有测试文件

修改内容：

1. `fetchMessages` 和 `loadConversation` 增加请求代际及 `conversationId` 校验。
2. 点击“新对话”时先使所有历史加载请求失效，再清空本地消息。
3. 快速选择 A、B 对话时，只有 B 的响应可以更新当前消息和活动对话 ID。
4. 页面首次进入时仍可自动载入最近对话，但该行为只属于一次显式的 bootstrap 状态；不能在作者点击“新对话”后再次自动载入旧对话。
5. 已有模型生成流继续使用现有 `AbortController`；历史 GET 请求与生成流的取消状态分开管理。
6. 迟到的旧对话响应不得恢复旧运行卡片、旧日志或旧立项 session。

完成标准：历史记录加载中点击“新对话”，即使旧请求随后成功，输入区和消息列表仍保持新对话状态。

### 阶段 D：收敛其他详情面板的“最后目标生效”规则（P2）

涉及文件：

- `frontend/src/pages/PromptPacksPage.tsx`
- `frontend/src/pages/CatalogingPage.tsx`
- `frontend/src/pages/DeconstructPage.tsx`
- `frontend/src/pages/SkillsPage.tsx`
- `frontend/src/pages/ContextGovernancePage.tsx`

修改内容：

- 提示词包：详情响应必须匹配当前 `selectedPackId`，避免列表高亮 B、编辑器显示 A。
- 建档任务：`loadJob` 的 job、runs、candidates 和 facts 必须作为同一目标代际提交，不能混合两个任务的数据。
- 拆书报告：旧报告响应不得覆盖最后选择的报告。
- 技能版本：版本抽屉关闭或切换技能后，旧版本列表不得写回。
- Manifest 详情：详情关闭或切换后，旧响应不得打开/覆盖当前抽屉。

这些页面只统一请求所有权，不改变现有 API 和业务流程。

## 6. 回归测试矩阵

测试统一使用可手动 resolve 的延迟 Promise，确定性控制响应顺序，避免依赖真实网络速度。

### 6.1 角色页

- [x] 列表已有角色时点击“＋”，列表刷新后仍显示“新角色”。
- [x] 角色 A 详情请求未完成时点击“＋”，A 迟到后不覆盖空白表单。
- [x] 快速选择 A 再选择 B，按 B→A 顺序返回时最终仍显示 B。
- [x] A 的版本历史和 AI 配置迟到后不写入 B 或新建态。
- [x] 新建态保存调用 `POST`，不会误调用旧角色的 `PUT`。
- [x] 创建成功后选中新角色，刷新列表不跳到第一名旧角色。
- [x] 删除当前角色后选择规则确定且不会循环请求。
- [x] 无角色时首次进入显示空状态，并可创建第一名角色。

### 6.2 立项资料编辑器

- [x] 先打开 A 再打开 B，A 最后返回仍保持 B。
- [x] 加载 A 时点击返回，A 返回后编辑器不会重新打开。
- [x] A 加载期间在 B 中输入，A 返回后 B 的输入和 dirty 状态不变。
- [x] A 保存未完成时切换到 B，A 保存成功不把编辑器切回 A。
- [x] 切换立项 session 后，旧 session 的详情和一致性结果不写入新 session。
- [x] revision 冲突保留本地内容，并显示当前目标对应的错误。

### 6.3 对话

- [x] 初始历史对话加载中点击“新对话”，迟到响应不恢复旧消息。
- [x] 快速选择对话 A、B，最终只显示 B。
- [x] 旧对话的运行卡片和日志不进入新对话。
- [x] 正在生成时仍遵循现有停止/取消规则，不因历史请求代际改变任务状态。

### 6.4 其他面板

- [x] 提示词包 A、B 乱序返回时，列表选中与编辑内容一致。
- [x] 建档任务的 job/runs/candidates/facts 不发生跨任务混合。
- [x] 拆书报告、技能版本和 Manifest 详情均符合最后选择生效。

## 7. 产品冒烟路径

### PC Web/Desktop

1. 创建一个测试作品并先保存角色 A。
2. 点击“＋”，创建角色 B，确认角色 A 未被修改且 B 正常进入列表。
3. 在模拟慢网络下重复“角色 A → 新增”“角色 A → 角色 B”。
4. 在立项资料中快速打开两个阶段，分别测试返回、关闭和自动保存。
5. 在系统助手与项目助手中测试“历史对话 → 新对话”和 A/B 快速切换。
6. 快速切换提示词包、建档历史和拆书报告，确认标题与详情一致。
7. 刷新页面并重新打开作品，确认数据与最后一次成功保存一致。

### Android / Gateway / API

- 后端契约无变化，不要求新增移动端实现。
- 使用现有 API 创建、查询一个角色，确认 PC 修复没有改变请求体和响应体。
- Android 若已有角色创建入口，执行一次独立创建与查询冒烟；不得依赖 PC 界面状态。
- Gateway 同步只验证新角色仍能按原契约同步，不新增分支或降级逻辑。

## 8. 验收标准

全部满足后方可判定完成：

- [x] 已有角色时可以稳定创建第二个及后续角色。
- [x] 新建态与首次初始化状态在类型和行为上明确分离。
- [x] 所有本计划涉及的详情请求只允许最新目标写回。
- [x] 关闭、新建、切换 session/对象时会使旧请求确定性失效。
- [x] 未保存输入不会被列表刷新、详情读取或旧保存响应覆盖。
- [x] 不新增备用 API、重复状态源或静默 fallback。
- [x] 后端和 Android 共享契约无漂移。
- [x] 新增回归测试通过，现有前端测试、lint、架构检查和构建通过。
- [ ] 关键慢网络 E2E 和 Windows 安装版冒烟通过。

最后一项由不可在 Linux 本地替代的 GitHub Release Gate、Playwright 浏览器和 Windows
安装包冒烟共同判定；发布前不预先勾选。当前本地验证结果：前端 47 个测试文件、
219 个测试全部通过，lint、quality、architecture、OpenAPI 类型检查和生产构建通过；
发布门禁使用的后端子集 172 个测试全部通过，移动端契约和性能基线检查通过。

建议验证命令：

```bash
cd frontend
npm test -- --run
npm run lint
npm run architecture
npm run api:check
npm run build
```

如变更公共 API 类型或跨端导出契约，还需运行完整后端与 Android 检查；本计划原则上不应触发这些契约变化。

## 9. 实施顺序与提交拆分

建议在一个 hotfix PR 中按可独立审查的提交拆分：

1. `test: reproduce character create and stale detail races`
2. `fix: make character editor target explicit`
3. `test: cover stale creation artifact responses`
4. `fix: fence creation artifact reads and saves`
5. `fix: ignore stale assistant history loads`
6. `fix: fence remaining detail panels`
7. `docs: record frontend async state guarantees`

测试应先证明旧代码失败，再提交实现使其通过。最终 PR 只保留修复后的权威测试，不保留临时复现脚本。

## 10. 发布方案

建议作为 `3.3.6` hotfix 发布：

1. 从最新 `main` 创建 `hotfix/frontend-editor-state-3.3.6`。
2. 完成 P0、P1 和必要 P2 修复及回归测试。
3. 同步版本号、README 和 `docs/release-notes-3.3.6.md`。
4. 提交 PR 合入 `main`，等待 Release Gate 全部通过。
5. 创建不可移动的 `v3.3.6` tag 并发布正式资产。
6. 下载正式资产复核版本、签名和 SHA-256，执行 Windows 安装冒烟。
7. 将最终提交同步回 `develop`。
8. 发布完成后删除临时 hotfix 分支。

本次不涉及数据库迁移，回滚方式为回退前端修复提交并重新构建；已经成功创建的角色和其他业务数据无需迁移或回滚。

## 11. 完成后文档更新

- 在 `docs/architecture/adr-006-frontend-state-and-contracts.md` 补充“最新交互目标拥有异步响应写回权”的前端契约。
- 在 3.3.6 发布说明中明确：修复已有角色时无法进入新增态，以及慢网络下旧详情覆盖当前编辑器的问题。
- PR 描述列出 PC、Android、Gateway/API 的影响判定和实际验证结果。
- 若实施中发现用户反馈来自立项 Agent 而非正式角色页，另建 issue 和修复计划，不在本 hotfix 中隐式扩大语义范围。
