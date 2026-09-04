# 2026-09-04 高可用分支与远程 main 对比

状态：这是合并前的只读比较快照。远程代码随后已在
`codex/ha-upstream-integration-20260904` 完成集成；最终决策、验证结果和已知限制见
[`2026-09-04-upstream-integration.md`](./2026-09-04-upstream-integration.md)。

## 比较对象

| 项目 | 值 |
| --- | --- |
| 共同基线 | `db97034a864f7f2851823c07ee83c953a5ddffca`（v3.3.12） |
| 本地成果分支 | `codex/ha-novel-soak-20260904` |
| 本地快照提交 | `cd54921edeac818ac9d9b886d06380e96e2cf74e` |
| 远程引用 | `origin/main` |
| 抓取后的远程提交 | `ef2fdc5ff7b7d3fd244f81e21ef30911c2849815` |
| 业务快照分叉情况 | 远程独有 41 个提交；本地独有 1 个提交 |

本段描述比较发生时的状态：当时工作树干净，远程通过 `git fetch --prune origin`
抓取，只更新远程引用，尚未执行 `merge` 或 `rebase`。

上表以业务快照 `cd54921` 为比较输入。将本报告提交到同一分支后，分支相对共同基线共有 2 个本地独有提交，其中第二个只包含本报告；远程独有提交数仍为 41。

## 改动规模

| 范围 | 文件 | 新增行 | 删除行 |
| --- | ---: | ---: | ---: |
| 本地成果相对共同基线 | 254 | 18,944 | 1,441 |
| 远程 main 相对共同基线 | 76 | 4,173 | 1,006 |

两边共同修改 23 个文件；本地独有 231 个，远程独有 53 个。23 个重叠文件中，Git 可自动处理 16 个，7 个存在内容冲突。自动合并只代表文本不重叠，不代表业务语义已经兼容。

完整机器可读结果位于 `artifacts/ha-novel-20260831/upstream-comparison.json`，虚拟合并原始输出位于 `artifacts/ha-novel-20260831/upstream-merge-tree.txt`，各冲突的 diff3 预览位于 `artifacts/ha-novel-20260831/upstream-conflict-previews/`。

## 远程更新的五组主要内容

### 1. 完整项目包引用修复

远程前三个提交修复完整项目包中 `narrative_checkpoints.chapter_id` 或 `chapter_snapshot_id` 指向包外实体时的导入失败，并补充 PC、Android 项目包测试。它修改导出过滤、结构包引用清理和验证器。

本地分支修复的是另一处项目包问题：v1 包回导时恢复被省略的大纲 `cataloging_status`，并区分首次导入与幂等重放文案。两组修复位于同一服务的不同区域，虚拟合并可以自动完成，功能互补；合并后仍必须同时跑远程新增的 checkpoint 用例和本地 41 章原包回导审计。

### 2. 章节修改/删除后的建档回滚

远程增加完整的章节建档回滚边界。语义修改或删除中间章节时，它回滚当前章及后续章节产生的人物、关系、世界资料、大纲、时间线、治理记录和 RAG 投影，标记后续章节重新建档；纯文风修改可通过显式 `X-Siming-Cataloging-Impact: style_only` 保留建档结果。正在运行的旧建档进程只在数据库事务提交后停止。

这是本地成果中没有的关键能力，应当引入。本地分支同时增加了跨进程建档启动锁、同章节版本任务复用、REST/工作区/CLI 统一启动与应用，以及当前世界资料过滤。两边在章节路由和建档 launcher 上发生直接冲突，必须组合事务回滚与幂等复用，不能采用简单的 ours/theirs。

### 3. 生成 OpenAPI 类型同步

远程重新生成了 OpenAPI 类型并增加一次性 CI 同步步骤。本地分支也因新增摘要修订、世界资料状态、建档回执等契约修改了生成文件。

合并后不能保留 Git 对 `frontend/src/api/generated/schema.d.ts` 的文本自动合并结果；必须从最终后端 OpenAPI 重新生成，再检查 PC 和 Android 使用的工具契约。

### 4. 未知模型使用 256K 有界兜底

远程把未知模型的默认上下文窗口从 1,000,000 调整为 256,000，并允许标记为 `unverified` 的请求使用按 UTF-8 字节计数的保守兜底，不再因缺少容量档案直接阻断。PC 与 Android 的容量策略同步更新。

本地分支在真实 OpenCode/Codex 使用中补齐了模型元数据发现、实际运行模型绑定和容量档案传递。两者可以组合为“优先使用真实档案，确实缺失时才使用 256K 未验证兜底”。需要保留 `capacity_assurance=unverified`，不能把兜底冒充供应商确认值；相关上下文预算和模型就绪测试必须按最终产品选择调整。

### 5. 取消原生工具调用数量限制并加强工具契约校验

远程最后一个提交删除单步最多 12 个原生工具调用的限制，并使用导出的 JSON Schema 在执行前校验参数；错误回执返回字段位置和失败规则，不包含原输入值。它还调整立项工具范围、手机工具分页和 PC/Android 提示契约。

本地分支也在执行器入口增加了 Pydantic 输入校验和脱敏错误，并依靠类别切换、事务、写入门禁、时限和上下文交付状态限制执行。合并时可取消固定调用数量，但必须继续保留批次原子校验、字节边界、写入次数边界、超时/取消和类别切换后结束当前步骤；否则会把远程的可用性改进变成无界运行风险。

## 七个内容冲突

| 文件 | 冲突块 | 双方意图 | 合并建议 |
| --- | ---: | --- | --- |
| `backend/app/modules/creation/domain/tool_specs.py` | 2 | 本地说明 `changes` 必须是原生对象数组；远程增加 `oneOf`、操作互斥验证、`min_length=1` 和导入文件分页 | 同时保留描述和 `min_length=1`；采用远程模型验证器及分页字段，继续禁止 JSON 字符串化 |
| `backend/app/routers/chapters.py` | 3 | 本地返回建档幂等复用状态并提供作者摘要修订；远程区分 semantic/style_only、回滚后续章节并批量重新建档 | 先执行远程回滚判定，再让本地 launcher 只复用仍有效的当前版本；消息同时表达后续章数量和复用状态 |
| `backend/app/services/cataloging/launcher.py` | 3 | 本地跨进程串行化、当前版本任务复用和统一 worker 入队；远程事务提交后停止被回滚的运行进程 | 两套能力并存；回滚事务提交后取消旧 worker，新启动必须经过本地锁且不得复用被标脏/取消的 run |
| `backend/app/services/rag/indexer.py` | 1 | 双方都修复先插入 `RagDocument` 再写 chunk 的外键顺序；本地还隔离失活世界资料、清理陈旧大纲索引 | 保留本地定向 `db.flush([doc])` 和当前态清理；用远程外键测试验证，不需要全 Session flush |
| `backend/app/services/workspace/executor.py` | 2 | 本地把 Pydantic 校验结果规范化后交给 handler，并返回脱敏错误；远程同时校验导出 JSON Schema并返回可修正的路径/规则 | 使用远程双层校验与结构化诊断，继续禁止回显输入；明确是否把规范化后的 model dump 传给 handler，并补默认值/别名测试 |
| `mobile/android/app/src/main/assets/pc_workspace_prompt_contract.json` | 1 | 本地加入完整上下文分页、硬字数下限、失活状态和工具说明；远程更新类别步骤、立项工具范围和手机分页契约 | 这是生成文件，不手工选边；后端契约合并完成后重新导出并核对哈希 |
| `mobile/android/app/src/main/java/com/siming/mobile/data/agent/MobileWorkspaceAgent.kt` | 7 | 本地实现当前世界资料过滤、精确大纲分页/总数/next_arguments、上下文交付门禁；远程增加通用分页器、参数契约验证并重写相同查询 | 以一个分页实现为权威；保留本地 active 过滤和完整游标语义，吸收远程共享分页/校验，删除重复 helper 后跑 Android 全量回归 |

## 文本可自动合并但需要重点复验的区域

- `backend/app/services/project_package_service.py`：两组项目包修复互补，但必须验证 full/structure、PC/Android 和 41 章旧包回导。
- `backend/app/services/context_orchestrator.py`、`creation_agent_turn_runtime.py`、`novel_creation_agent.py`：远程容量兜底和步骤策略会改变本地真实模型路径的门禁结果。
- `backend/app/services/workspace/tool_result_projection.py`、`assistant_public_errors.py`：远程删除固定调用数量错误，本地增加更细的失败分类；自动合并后可能留下过时常量或不可达错误码。
- `contracts/fixtures/conversation-context-v1-interop.json`：必须重新生成/核对契约哈希，不能只接受文本自动合并。
- `frontend/src/api/generated/schema.d.ts`：必须从最终 OpenAPI 重建。
- `MobileConversationContext.kt`、`MobileCreationConversationAgent.kt` 及测试：需验证 PC/Android 的类别、分页、容量和回执语义仍一致。

## 集成风险排序

1. **最高风险：章节回滚与建档幂等复用。** 若顺序错误，语义修改后的章节可能错误复用旧建档，或回滚事务失败却提前杀死 worker。
2. **高风险：手机 Agent 查询与生成契约。** 两边独立修改分页和提示契约，直接拼接会出现重复实现、总数误报或 PC/Android 差异。
3. **已确认：未知模型 256K 兜底。** 产品选择为“精确档案优先，缺档案才使用 256K”；集成必须保留未验证标记与有界发送条件。
4. **中风险：工具 Schema 校验。** 远程结构化错误更利于模型修正，但需和本地脱敏、默认值规范化及原生数组约束组合。
5. **低风险：RAG 父记录 flush。** 双方修复同一根因，采用定向 flush 即可。

## 建议的后续集成顺序

1. 从当前成果分支再创建临时集成分支，合并 `origin/main`；不要在已验证的 `cd54921` 上直接解决冲突。
2. 先接入远程项目包引用修复和章节回滚模块，再解决章节路由/launcher，使“回滚使旧 run 失效”先于“当前版本 run 复用”。
3. 合并工具 Schema 与执行器，保留结构化可修正错误、脱敏和原生对象数组约束。
4. 合并模型容量策略：真实元数据优先，256K 只作为明确标记的有界兜底。
5. 统一 Android 分页实现，重新生成 PC→Android 提示契约与前端 OpenAPI 类型。
6. 先跑双方新增的定向测试，再跑后端、前端和 Android 全量；最后用现有 41 章项目包再次回导，并增加“修改中间章→后续全部失效→重新建档”的真实场景。

本次仅完成分支固化、远程抓取和比较。由于虚拟合并存在 7 个内容冲突且至少 3 个高风险语义交叉点，当前不适合直接 `git pull` 生成合并提交。
