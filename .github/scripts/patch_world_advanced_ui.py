from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


# Advanced dialog
path = Path("mobile/android/app/src/main/java/com/siming/mobile/ui/AdvancedAuthoringDialogs.kt")
text = path.read_text(encoding="utf-8")
anchor = '''@Composable\nprivate fun RelationshipEditor(\n'''
world_ui = '''private enum class WorldAdvancedTab(val label: String) {\n    Versions("版本"),\n    Timeline("时间线"),\n}\n\n@Composable\ninternal fun WorldAdvancedDialog(\n    projectId: String,\n    entry: ReplicaEntity,\n    online: Boolean,\n    viewModel: MainViewModel,\n    onDismiss: () -> Unit,\n) {\n    val scope = rememberCoroutineScope()\n    var tab by remember { mutableStateOf(WorldAdvancedTab.Versions) }\n    var loading by remember { mutableStateOf(false) }\n    var versions by remember { mutableStateOf<List<JsonObject>>(emptyList()) }\n    var timeline by remember { mutableStateOf<List<JsonObject>>(emptyList()) }\n\n    LaunchedEffect(entry.entityId, online) {\n        if (!online) return@LaunchedEffect\n        loading = true\n        try {\n            versions = viewModel.worldVersions(projectId, entry.entityId).arrayObjects("items")\n            timeline = viewModel.worldTimeline(projectId, entry.entityId).arrayObjects("items")\n        } catch (error: Exception) {\n            viewModel.reportError(error.toUserFacingMessage())\n        } finally {\n            loading = false\n        }\n    }\n\n    AlertDialog(\n        onDismissRequest = onDismiss,\n        title = { Text("${entry.text("title").ifBlank { "世界观条目" }} · 历史") },\n        text = {\n            Column(\n                Modifier.heightIn(max = 600.dp).verticalScroll(rememberScrollState()),\n                verticalArrangement = Arrangement.spacedBy(10.dp),\n            ) {\n                if (!online) {\n                    Text("世界观版本和时间线由 PC 维护，需要连接 Gateway 才能查看。")\n                    return@Column\n                }\n                Text(\n                    "世界观关系目前没有 PC 专用 HTTP 编辑路由，手机只保留同步数据，不自行发明写接口。",\n                    style = MaterialTheme.typography.bodySmall,\n                    color = MaterialTheme.colorScheme.onSurfaceVariant,\n                )\n                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {\n                    WorldAdvancedTab.entries.forEach { item ->\n                        AssistChip(onClick = { tab = item }, label = { Text(item.label) })\n                    }\n                }\n                if (loading) CircularProgressIndicator()\n                when (tab) {\n                    WorldAdvancedTab.Versions -> {\n                        if (versions.isEmpty() && !loading) Text("暂无世界观版本记录。")\n                        versions.forEach { version ->\n                            OutlinedCard(Modifier.fillMaxWidth()) {\n                                Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {\n                                    Text("v${version.int("version_number")}", fontWeight = FontWeight.SemiBold)\n                                    Text(version.string("change_summary").ifBlank { "无变更摘要" })\n                                    version.string("source_chapter_id").takeIf(String::isNotBlank)?.let {\n                                        Text("来源章节：$it", style = MaterialTheme.typography.bodySmall)\n                                    }\n                                    Text(version.string("created_at"), style = MaterialTheme.typography.labelSmall)\n                                }\n                            }\n                        }\n                    }\n                    WorldAdvancedTab.Timeline -> {\n                        if (timeline.isEmpty() && !loading) Text("暂无世界观时间线事件。")\n                        timeline.forEach { event ->\n                            OutlinedCard(Modifier.fillMaxWidth()) {\n                                Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {\n                                    Text(\n                                        "#${event.int("sort_order")} · ${event.string("event_type").ifBlank { "event" }}",\n                                        fontWeight = FontWeight.SemiBold,\n                                    )\n                                    Text(event.string("event_description").ifBlank { "无事件描述" })\n                                    event.string("evidence").takeIf(String::isNotBlank)?.let {\n                                        Text("证据：$it", style = MaterialTheme.typography.bodySmall)\n                                    }\n                                    event.string("chapter_id").takeIf(String::isNotBlank)?.let {\n                                        Text("章节：$it", style = MaterialTheme.typography.bodySmall)\n                                    }\n                                }\n                            }\n                        }\n                    }\n                }\n            }\n        },\n        confirmButton = { TextButton(onClick = onDismiss) { Text("关闭") } },\n    )\n}\n\n'''
if anchor not in text:
    raise RuntimeError("world dialog anchor missing")
text = text.replace(anchor, world_ui + anchor, 1)
path.write_text(text, encoding="utf-8")

# SimingApp entry
path = Path("mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''                    onAdvanced = if (section in setOf("chapter", "character")) {\n''',
    '''                    onAdvanced = if (section in setOf("chapter", "character", "world")) {\n''',
    "world advanced callback",
)
anchor = '''                "character" -> CharacterAdvancedDialog(\n                    projectId = project.projectId,\n                    character = record,\n                    online = connection != null,\n                    viewModel = viewModel,\n                    onDismiss = { advanced = null },\n                )\n'''
addition = anchor + '''                "world" -> WorldAdvancedDialog(\n                    projectId = project.projectId,\n                    entry = record,\n                    online = connection != null,\n                    viewModel = viewModel,\n                    onDismiss = { advanced = null },\n                )\n'''
text = replace_once(text, anchor, addition, "world advanced dialog branch")
old_label = '''                                "character" -> if (advancedEnabled) "关系 / AI / 版本" else "高级资料需连接 PC"\n                                else -> "高级资料"\n'''
new_label = '''                                "character" -> if (advancedEnabled) "关系 / AI / 版本" else "高级资料需连接 PC"\n                                "world" -> if (advancedEnabled) "版本 / 时间线" else "历史需连接 PC"\n                                else -> "高级资料"\n'''
text = replace_once(text, old_label, new_label, "world advanced button label")
path.write_text(text, encoding="utf-8")
