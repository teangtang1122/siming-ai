---
id: continuity.cataloging.external
version: 3.0.0
scope: cataloging
visibility: public
inputs: []
output_format: text
tool_policy: cataloging_worker
tools:
  - get_prompt_pack
  - start_external_cataloging_job
  - get_next_external_cataloging_chapter
  - save_external_cataloging_candidates
  - apply_pending_cataloging
  - verify_external_cataloging_progress
  - get_project_archive_status
fragments: [continuity.cataloging.merged]
budget:
  fixed_chars: 9000
  context_chars: 80000
golden_cases:
  - name: external-workflow
    required_text: ["project_id", "phase=\"merged\"", "读取章节正文和档案镜像", "旧两阶段残留"]
  - name: shared-granularity
    required_text: ["内部建档、外部 MCP 建档、本机 CLI 建档", "parent_title", "验证"]
---
【外部 Agent 工作流】
1. 先用 list_projects 确认作品。current_project_id 为空时也必须选择真实 project_id；本轮所有读写和验证调用使用同一个 project_id。
2. 调用 get_prompt_pack 读取本提示词，再用 start_external_cataloging_job 创建任务；不要调用内部 start_cataloging_job 消耗司命模型。
3. 串行执行 get_next_external_cataloging_chapter，使用 phase="merged"，自己读取章节正文和档案镜像。
4. 调用 save_external_cataloging_candidates 保存标准候选，再 apply_pending_cataloging；每章立即 verify_external_cataloging_progress 验证。
5. 前一章完成应用和验证后才能领取下一章。只有工具明确提示旧两阶段残留时，才使用 facts/candidates 旧阶段。
6. 全部完成后调用 get_project_archive_status；status 不是 ok 时停止并报告，不得宣布完成。

内部建档、外部 MCP 建档、本机 CLI 建档使用同一颗粒度。直接写 chapters 文件不算入库，所有修改必须通过司命工具。
