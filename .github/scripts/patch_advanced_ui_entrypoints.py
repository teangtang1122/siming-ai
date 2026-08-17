from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


path = Path("mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''    var section by rememberSaveable(project.projectId) { mutableStateOf("assistant") }\n    var editor by remember { mutableStateOf<EditorTarget?>(null) }\n''',
    '''    var section by rememberSaveable(project.projectId) { mutableStateOf("assistant") }\n    var editor by remember { mutableStateOf<EditorTarget?>(null) }\n    var advanced by remember { mutableStateOf<EditorTarget?>(null) }\n    var showChapterOrder by remember { mutableStateOf(false) }\n''',
    "advanced ProjectScreen state",
)
old_call = '''                RecordList(\n                    section = requireNotNull(currentSection),\n                    records = records,\n                    online = connection != null,\n                    onOpen = { editor = EditorTarget(section, it) },\n                )\n'''
new_call = '''                RecordList(\n                    section = requireNotNull(currentSection),\n                    records = records,\n                    online = connection != null,\n                    onOpen = { editor = EditorTarget(section, it) },\n                    onAdvanced = if (section in setOf("chapter", "character")) {\n                        { record -> advanced = EditorTarget(section, record) }\n                    } else {\n                        null\n                    },\n                    onManageChapterOrder = if (section == "chapter") {\n                        { showChapterOrder = true }\n                    } else {\n                        null\n                    },\n                )\n'''
text = replace_once(text, old_call, new_call, "RecordList call")
scaffold_end = '''    }\n}\n\n@Composable\nprivate fun RecordList(\n'''
dialogs = '''    }\n\n    if (showChapterOrder) {\n        ChapterOrderDialog(\n            projectId = project.projectId,\n            chapters = records,\n            online = connection != null,\n            viewModel = viewModel,\n            onDismiss = { showChapterOrder = false },\n        )\n    }\n    advanced?.let { target ->\n        val record = target.record\n        if (record != null) {\n            when (target.entityType) {\n                "chapter" -> ChapterHistoryDialog(\n                    projectId = project.projectId,\n                    chapter = record,\n                    online = connection != null,\n                    viewModel = viewModel,\n                    onDismiss = { advanced = null },\n                )\n                "character" -> CharacterAdvancedDialog(\n                    projectId = project.projectId,\n                    character = record,\n                    online = connection != null,\n                    viewModel = viewModel,\n                    onDismiss = { advanced = null },\n                )\n            }\n        }\n    }\n}\n\n@Composable\nprivate fun RecordList(\n'''
text = replace_once(text, scaffold_end, dialogs, "advanced dialog render")
text = replace_once(
    text,
    '''private fun RecordList(\n    section: EntitySection,\n    records: List<ReplicaEntity>,\n    online: Boolean,\n    onOpen: (ReplicaEntity) -> Unit,\n) {\n''',
    '''private fun RecordList(\n    section: EntitySection,\n    records: List<ReplicaEntity>,\n    online: Boolean,\n    onOpen: (ReplicaEntity) -> Unit,\n    onAdvanced: ((ReplicaEntity) -> Unit)?,\n    onManageChapterOrder: (() -> Unit)?,\n) {\n''',
    "RecordList signature",
)
old_heading = '''        item {\n            ScreenHeading(\n                kicker = section.type.uppercase(),\n                title = section.label,\n                detail = when (section.type) {\n'''
new_heading = '''        item {\n            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {\n                ScreenHeading(\n                    kicker = section.type.uppercase(),\n                    title = section.label,\n                    detail = when (section.type) {\n'''
text = replace_once(text, old_heading, new_heading, "heading column start")
old_heading_end = '''                },\n            )\n        }\n        if (records.isEmpty()) {\n'''
new_heading_end = '''                    },\n                )\n                if (onManageChapterOrder != null) {\n                    OutlinedButton(\n                        onClick = onManageChapterOrder,\n                        enabled = online && records.size > 1,\n                    ) {\n                        Text(if (online) "管理章节顺序" else "章节排序需要 PC Gateway")\n                    }\n                }\n            }\n        }\n        if (records.isEmpty()) {\n'''
text = replace_once(text, old_heading_end, new_heading_end, "heading column end")
text = replace_once(
    text,
    '''                RecordCard(section.type, record, onClick = { onOpen(record) })\n''',
    '''                RecordCard(\n                    section.type,\n                    record,\n                    onClick = { onOpen(record) },\n                    onAdvanced = onAdvanced?.let { callback -> { callback(record) } },\n                    advancedEnabled = online,\n                )\n''',
    "RecordCard list call",
)
text = replace_once(
    text,
    '''private fun RecordCard(entityType: String, record: ReplicaEntity, onClick: () -> Unit) {\n''',
    '''private fun RecordCard(\n    entityType: String,\n    record: ReplicaEntity,\n    onClick: () -> Unit,\n    onAdvanced: (() -> Unit)? = null,\n    advancedEnabled: Boolean = false,\n) {\n''',
    "RecordCard signature",
)
card_tail = '''            Text(\n                "修订 ${record.revision} · ${if (record.dirty) "本机有新修改" else "已写入离线库"}",\n                style = MaterialTheme.typography.labelSmall,\n                color = MaterialTheme.colorScheme.onSurfaceVariant,\n            )\n        }\n    }\n}\n'''
card_tail_new = '''            Text(\n                "修订 ${record.revision} · ${if (record.dirty) "本机有新修改" else "已写入离线库"}",\n                style = MaterialTheme.typography.labelSmall,\n                color = MaterialTheme.colorScheme.onSurfaceVariant,\n            )\n            if (onAdvanced != null) {\n                Row(\n                    Modifier.fillMaxWidth(),\n                    horizontalArrangement = Arrangement.End,\n                ) {\n                    TextButton(\n                        onClick = onAdvanced,\n                        enabled = advancedEnabled,\n                    ) {\n                        Text(\n                            when (entityType) {\n                                "chapter" -> if (advancedEnabled) "版本历史" else "版本需连接 PC"\n                                "character" -> if (advancedEnabled) "关系 / AI / 版本" else "高级资料需连接 PC"\n                                else -> "高级资料"\n                            },\n                        )\n                    }\n                }\n            }\n        }\n    }\n}\n'''
text = replace_once(text, card_tail, card_tail_new, "RecordCard advanced action")
path.write_text(text, encoding="utf-8")
