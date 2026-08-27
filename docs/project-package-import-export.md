# 书籍项目导入导出（项目包）调用说明

> 功能：把数据库里的正式作品（已立项的书）完整导出为可移植的项目包 ZIP；导入时自动创建全新的作品，数据与导出前一致（大纲、设定、立项资料等完整保留，ID 全部重新生成）。

后端实现：
- 接口：`backend/app/routers/project_package.py`
- 服务：`backend/app/services/project_backup_service.py`（`ProjectBackupBuilder` / `ProjectBackupRestorer`）
- 测试：`backend/tests/test_project_package.py`

---

## 1. 导出项目包

### 接口

```
POST /api/v1/projects/{project_id}/project-package
```

Query 参数：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `include_chapters` | bool | `true` | `true` 导出完整项目（含章节正文）；`false` 只导出大纲、设定与立项资料等结构化数据，不含正文 |

### 示例

```bash
# 完整导出（含正文，"和导出前一样"）
curl -X POST "http://localhost:8000/api/v1/projects/{project_id}/project-package" \
  -o 项目导出.zip

# 只导出大纲 / 设定 / 立项资料（不含章节正文）
curl -X POST "http://localhost:8000/api/v1/projects/{project_id}/project-package?include_chapters=false" \
  -o 项目包.zip
```

### 响应

- `200 OK`，`Content-Type: application/zip`。
- `Content-Disposition` 同时携带 ASCII 兜底文件名与 RFC 5987 编码的中文文件名：

```
attachment; filename="export_xxxxxxxx.zip"; filename*=UTF-8''%E4%B8%87%E8%B1%A1%E5%BD%92%E5%A2%9F_%E9%A1%B9%E7%9B%AE%E5%AF%BC%E5%87%BA_20260827.zip
```

---

## 2. 导入项目包（创建新作品）

### 接口

```
POST /api/v1/projects/project-package/import
```

multipart 表单字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | 文件 | 是 | 项目包 ZIP（由导出接口生成） |
| `new_title` | string | 否 | 导入后使用的新标题；缺省沿用包内标题 |

### 示例

```bash
# 导入为全新作品（沿用原标题）
curl -X POST "http://localhost:8000/api/v1/projects/project-package/import" \
  -F "file=@项目导出.zip"

# 导入并改名
curl -X POST "http://localhost:8000/api/v1/projects/project-package/import" \
  -F "file=@项目导出.zip" \
  -F "new_title=万象归墟·重制版"
```

### 响应

```json
{
  "code": 0,
  "message": "项目导入成功：已创建作品「万象归墟」",
  "data": {
    "project_id": "0517eb2e-a90e-4dd1-a907-57e3e6540929",
    "project_title": "万象归墟",
    "counts": {
      "chapters": 120,
      "outline_nodes": 158,
      "characters": 35,
      "worldbuilding_entries": 42,
      "novel_creation_sessions": 1,
      "novel_creation_runs": 26,
      "novel_creation_events": 40,
      "novel_creation_entities": 18,
      "novel_creation_artifact_versions": 12,
      "novel_creation_claims": 8,
      "novel_creation_material_imports": 1,
      "novel_creation_import_chunks": 6,
      "assistant_conversations": 5,
      "assistant_runs": 5,
      "rag_documents": 90,
      "scheduled_tasks": 0
    },
    "preserved_ids": false
  }
}
```

导入成功后返回的 `project_id` 即新作品 ID，前端可用它跳转到项目工作区。

---

## 3. 项目包内容

ZIP 内为 JSON 文件集合，格式沿用 `siming-project-backup` v1.0：

| 文件 | 内容 |
|---|---|
| `manifest.json` | 格式、版本、导出时间、内容计数 |
| `project.json` | 作品基础信息（标题、简介、标签、写作风格等） |
| `outline.json` / `outline_characters.json` | 大纲树与大纲角色关联 |
| `characters*.json` | 角色、别名、版本、时间线、关系 |
| `worldbuilding*.json` | 世界观条目、版本、时间线、关联 |
| `chapters*.json` | 章节正文、快照、摘要、质量指标（`include_chapters=false` 时剥离） |
| `novel_creation_*.json` | 立项数据：立项会话（含创作约束/创意方向/各阶段大纲与设定）、阶段运行、事件、实体、art 版本历史、幂等声明、素材导入记录 |
| `assistant_*.json` / `rag_*.json` / `scheduled_tasks.json` / `narrative_*.json` | 对话、索引、自动任务、叙事治理等 |

恢复时所有实体生成新 UUID，包内引用通过 ID 映射保持一致；不属于项目范围的全局运行态（`operation_runs`、`context_manifests`）相关外键置空。

---

## 4. 限制与注意

- 仅支持后端导出的项目包（`siming-project-backup` 格式）；其他 ZIP 会被拒绝。
- 默认完整导出（含正文）；仅分享大纲/设定/立项资料时使用 `include_chapters=false`。
- 导入总会创建**新**作品，不会覆盖已有作品；如需恢复到既有作品请使用底层备份恢复接口。
- 项目包可能包含未公开的正文内容，分享前请确认目标对象。

---

## 5. 前端接入建议（供原作者协调）

- 项目工作区「导出」页可增加「导出项目包」按钮：调用导出接口后，浏览器直接下载 ZIP（已是附件下载响应，无需再走文件落盘 + 下载两步）。
- 作品库（Dashboard）可增加「导入项目包」入口：`Upload` 组件上传 ZIP 到导入接口，成功后用返回的 `project_id` 跳转新作品。
- 移动端如需支持，可复用同一组接口（文件上传走 multipart）。
