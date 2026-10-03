# 最终审阅必须有保存回执

## 现场证据

- 作品：《我靠给反派改命飞升了》。立项 ID：`4b1d2960-b00b-4e14-af72-6ff231fbc3e8`。
- 作者发送“最终审阅”后，模型选择了立项会话、阶段资料类别，成功读取会话、资料列表和七个阶段资料。
- 本轮没有调用任何写入工具，`write_count=0`；聊天却声称最终审阅已经完成。
- 数据库当时为 revision 16，`final_review.status=pending`，`data=null`。右侧“待生成”准确反映了实际状态。

## 修复边界

- 在权威立项提示词中明确：生成、修改及最终审阅必须调用写工具保存阶段资料，聊天报告不能代替资料。
- 实际业务读取成功且本轮尚无成功写入时，后续模型步骤增加同一条运行时提醒。仍由模型根据作者最新消息决定查看、生成或修改以及选择目标和工具；应用不解析自然语言意图。
- 本轮最终仍只有成功读取时，回复标为 `read_only` 并明确提示“本轮只读取了立项资料，尚未生成或保存新的阶段资料”。阶段状态不因聊天文本改变。
- PC、API 和本机 CLI/MCP 使用权威立项提示词及运行时；手机端独立执行路径通过导出的同一契约使用相同提醒和回复语义。
- 未增加模型请求、自动确认阶段或自动创建正式作品。模型仍可能选错或不调用工具，结果以实际工具回执和持久化状态为准。

## 回归

- 后端：`test_creation_read_only_completion`、`test_creation_entity_read_admission`、`test_novel_creation_agent`、`test_mobile_prompt_contract`、`test_direct_mcp_run_steps`，122 项通过。
- Android：`MobileCreationConversationAgentTest`、`PcCreationAgentContractTest`，35 项通过。
- 回归覆盖纯读取回复不改变阶段状态；模型选择写入后保存结构化审阅；手机端独立生成、保存审阅，并返回等待作者确认。
- 安装包已校验 SHA256 并安装到 `D:\Siming`；安装程序日志确认成功，安装后的程序与构建产物一致，健康接口返回 200。

## 实际重试

- 首次重试的流事件保存在本机忽略目录 `.build/review-receipt-local-run.jsonl`。七阶段资料及实体读取成功，但模型切回阶段资料类别时，原生工具事务被容量校验拦截，尚未写入审阅。
- 实际失败请求包含 22 条完整消息。原预算按 UTF-8 字节计了 58,707 Token，只剩 685；类别切换事务需要 14,790，整批未执行。
- 已将受管理本地模型的权威计数更新为完整请求模板和已加载分词器实测；保留全部原生思考、工具调用及结果，保留工具事务执行前的保守校验。
- 同一失败请求实测模板消息 14,379 Token，工具计数及边界余量 1,751；加上检查余量后保守输入边界为 16,642，剩余 42,750，原事务可容纳。
- 分词计数绑定本地模型运行实例和工具 schema；实例变化或绑定后测量不可用会明确失败，不能复用缓存或在回合中暗中改变计数方法。
- 后端追加完整模板、模型切换及测量失败回归后，相关 161 项通过。
- 平台计数差异：完整模板实测需要受管理 llama.cpp 实例的授权分词接口；PC 后端具备该条件。手机独立 DirectApi 和外部 API 未具备该接口时仍使用显式标识的保守字节计数，业务读写契约和保存状态不变。手机独立流程的 35 项回归通过。

- 第二次原话“最终审阅”重试正常结束，耗时 305 秒。模型选择 `patch_creation_artifact`，保存回执为 `ok`，revision 17，`final_review.status=generated`；上游七个阶段状态及内容逐项比较完全不变，也未自动确认或创建作品。
- 持久化核对同时发现该报告使用自定义 `readiness` 字段，缺少界面必需的布尔值 `ready`。已在 PC 写入边界补齐与手机一致的布尔结构检查，并明确共享提示中的 ready、blocking、warnings、counts 契约；不根据别名或文本推导 ready。
- 最终回归包含原生预算、上下文事务、阶段写入及保存回执：后端 221 项通过。Android 独立流程、布尔结构拒绝和共享契约：36 项通过。

- 同一本地模型随后成功修正保存的报告为规范字段，revision 18，`ready=true`、`blocking=[]`，原有三条风险和审阅结论保持不变；未执行确认或创建作品。
- 核对又发现报告把计划全书 240 章填入了 UI 表示实际开篇细纲数的 `counts.chapters`。已明确 PC/手机共享提示的数量口径；模型读取实际 opening_outline 后修正为 3，并将计划章数存入 `counts.target_chapters=240`，revision 19。
- 最终数据库与重新打开的已安装程序 API 均确认：`final_review.status=generated`、`ready=true`；前端当前映射为“待确认”。七个上游阶段逐项比较完全不变，`created_project_id=null`，`last_error=null`，无正在运行的任务。
- 最终共享提示及只读回执的 10 项定向复核通过。最终安装包 SHA256：`22497ab7da9ec85649282516617a56d13d9b98409419c80a479681eda23ff028`；安装日志确认成功，程序与 payload 一致，启动后的健康接口返回 200。
- 最终实际验证保存在本机 `.build/review-receipt-final-verification.json`；结构修正及数量修正的流事件分别在 `.build/review-receipt-local-structure-repair.jsonl`、`.build/review-receipt-local-count-repair.jsonl`。
