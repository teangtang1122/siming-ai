# 项目管理指南

本文档用于规定司命后续开发、修复、发布和反馈管理方式。目标是减少“想到哪改到哪”，让每次推进都能留下可追踪的任务、验证和发布记录。

## 优先级定义

| 优先级 | 含义 | 示例 |
|---|---|---|
| P0 | 阻断核心工作流或可能造成数据损坏 | 作品无法打开、章节丢失、建档结果写错角色 |
| P1 | 影响主要功能稳定性 | 模型路由错误、建档无法继续、打包版无法启动 |
| P2 | 影响体验但有替代路径 | 错误提示不清楚、候选项筛选不方便 |
| P3 | 增强、优化、文档 | 新模型适配、界面优化、贡献指南 |

## Issue 规则

每个 issue 应该回答四个问题：

1. 用户遇到什么问题或想完成什么任务？
2. 当前行为是什么？
3. 期望行为是什么？
4. 如何验证它已经完成？

建议标签：

- `bug`：明确错误或回归。
- `enhancement`：功能增强。
- `docs`：文档或说明。
- `testing`：测试覆盖。
- `release`：版本、打包、发布。
- `cataloging`：作品建档。
- `model-routing`：模型选择、provider、API/CLI 路由。
- `local-models`：本地模型、llama.cpp、LoRA。
- `external-agent`：外部 Agent、MCP、CLI。

## 分支与 PR

推荐分支命名：

- `fix/cataloging-candidate-validation`
- `fix/model-routing-defaults`
- `docs/release-checklist`
- `test/cataloging-regression`
- `feat/context-preview`

PR 描述至少包含：

- 改了什么。
- 为什么改。
- 用户影响。
- 数据兼容或迁移风险。
- 已执行验证。

## 主分支保护

`main` 是发布基线，默认不直接提交代码。建议在 GitHub 仓库设置中启用 branch protection 或 ruleset，并至少打开以下规则：

- 目标分支：`main`。
- Require a pull request before merging。
- Require approvals：个人项目可设为 0 或 1；如果只有自己维护，重点是强制走 PR。
- Dismiss stale pull request approvals when new commits are pushed。
- Require status checks to pass before merging：有 CI 后再启用必需检查；没有 CI 前不要勾选不存在的检查。
- Require branches to be up to date before merging：有稳定 CI 后启用。
- Block force pushes。
- Block deletions。
- Do not allow bypassing the above settings：如果希望自己也被规则约束，则启用。

个人项目的最低保护策略：禁止 force push 和删除 `main`，所有变更通过 PR 合并。等测试和构建 CI 建好后，再把后端测试、前端构建和打包验证加入必需检查。

## 开发检查清单

后端变更：

- [ ] 影响数据库字段时检查迁移和旧数据兼容。
- [ ] 影响长任务时检查失败恢复、取消、重试。
- [ ] 影响模型输出解析时补异常输出样例。
- [ ] 影响写入流程时检查数据库、文件镜像、索引、缓存是否同步。

前端变更：

- [ ] loading、error、empty 状态完整。
- [ ] 长任务有进度或日志反馈。
- [ ] 删除、批量应用、覆盖写入前有确认。
- [ ] 窄屏和小窗口不遮挡关键操作。

AI/提示词变更：

- [ ] 明确模型输出 schema。
- [ ] 不用模板内容冒充模型生成结果。
- [ ] 解析失败时展示可诊断错误，而不是静默兜底。
- [ ] 中文小说资料保持中文字段和中文内容。

## 发布检查清单

发布前：

- [ ] `backend/app/version.py` 版本号已更新。
- [ ] `frontend/package.json` 版本号已更新。
- [ ] README 顶部变更说明已同步。
- [ ] 后端相关测试已运行。
- [ ] 前端 `npm run build` 已运行。
- [ ] 打包脚本已执行并生成 `Siming.exe`。
- [ ] `update.json` 和 `sha256.txt` 已生成。
- [ ] Release 说明包含重点变化、修复、兼容性、验证、已知问题。

发布后：

- [ ] 从 Release 下载包重新启动验证。
- [ ] 验证新建作品、导入作品、建档、写作、保存。
- [ ] 验证旧数据目录识别与迁移。
- [ ] 验证至少一种 API 模型和一种本地/CLI 模型路径。

## 每周维护节奏

建议每周做一次轻量整理：

1. 关闭已完成或重复 issue。
2. 把新反馈归类到 P0-P3。
3. 选出下一个版本最多 3 个主目标。
4. 检查 README 和实际版本是否一致。
5. 记录一个“本周最容易踩坑的问题”。

## 当前建议焦点

短期不要继续大量堆新功能。优先把以下三块做稳：

1. 模型路由：显式选择、全局默认、任务模型、本地 CLI 的优先级必须可测试。
2. 建档：候选解析、去重、应用、失败恢复必须可靠。
3. 发布：README、版本号、打包产物、Release 说明必须同步。
