---
id: continuity.cataloging.external
version: 3.1.15
scope: cataloging
visibility: public
inputs: []
output_format: text
tool_policy: cataloging_worker
tools:
  - get_prompt_pack
  - start_external_cataloging_job
  - get_next_external_cataloging_chapter
  - save_external_cataloging_facts
  - list_cataloging_facts
  - save_external_cataloging_candidates
  - apply_pending_cataloging
  - verify_external_cataloging_progress
  - get_project_archive_status
fragments: [continuity.cataloging.facts, continuity.cataloging.candidates]
budget:
  fixed_chars: 10000
  context_chars: 80000
golden_cases:
  - name: external-workflow
    required_text: ["project_id", "phase=\"facts\"", "phase=\"candidates\"", "读取章节正文和档案镜像", "逐章"]
  - name: shared-granularity
    required_text: ["内部建档、外部 MCP 建档、本机 CLI 建档", "coverage_manifest", "relationships", "character_profiles", "未具名岗位", "narrative_review", "resolves_item_id", "验证"]
---
【外部 Agent 工作流】
1. 先用 list_projects 确认作品。current_project_id 为空时也必须选择真实 project_id；本轮所有读写和验证调用使用同一个 project_id。
2. 调用 get_prompt_pack 读取本提示词，再用 start_external_cataloging_job 创建任务；不要调用内部 start_cataloging_job 消耗司命模型。
3. 逐章串行建档：先用 phase="facts" 领取当前章，自己读取章节正文和档案镜像，按规范一次性调用 save_external_cataloging_facts。
4. 再用 phase="candidates" 领取同一章，调用 list_cataloging_facts；has_more=true 时按 next_arguments 读完所有页，再结合当前档案生成完整候选并调用 save_external_cataloging_candidates。
5. 调用 apply_pending_cataloging，并立即 verify_external_cataloging_progress。前一章应用并验证完成后才能领取下一章。
6. 工具返回结构校验或覆盖缺项时，当前 Agent 只补齐工具明确列出的缺项；不得让系统猜测或伪造缺失资料。
7. 全部完成后调用 get_project_archive_status；status 不是 ok 时停止并报告，不得宣布完成。

内部建档、外部 MCP 建档、本机 CLI 建档使用同一颗粒度。直接写 chapters 文件不算入库，所有修改必须通过司命工具。
