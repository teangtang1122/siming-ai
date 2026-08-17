package com.siming.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.network.PcEditableRelationship
import com.siming.mobile.data.network.pcEditableRelationships
import com.siming.mobile.data.network.pcNewRelationship
import com.siming.mobile.data.network.pcRelationshipMutationPayload
import com.siming.mobile.data.network.mobileRefreshWarning
import com.siming.mobile.data.toUserFacingMessage
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put

@Composable
internal fun ChapterOrderDialog(
    projectId: String,
    chapters: List<ReplicaEntity>,
    online: Boolean,
    viewModel: MainViewModel,
    onDismiss: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var ordered by remember(chapters.map(ReplicaEntity::entityId)) { mutableStateOf(chapters) }
    var saving by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = { if (!saving) onDismiss() },
        title = { Text("章节阅读顺序") },
        text = {
            Column(
                Modifier.heightIn(max = 560.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    if (online) {
                        "顺序会一次性提交给 PC 的章节重排接口，由 PC 统一维护 authoritative sort_order。"
                    } else {
                        "需要连接 PC Gateway 才能重排；离线状态不会在手机端猜测 sort_order。"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                ordered.forEachIndexed { index, chapter ->
                    OutlinedCard(Modifier.fillMaxWidth()) {
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text("${index + 1}", style = MaterialTheme.typography.labelLarge)
                            Spacer(Modifier.width(10.dp))
                            Text(
                                chapter.text("title").ifBlank { "未命名章节" },
                                modifier = Modifier.fillMaxWidth(0.58f),
                            )
                            TextButton(
                                enabled = index > 0 && !saving,
                                onClick = {
                                    val copy = ordered.toMutableList()
                                    val item = copy.removeAt(index)
                                    copy.add(index - 1, item)
                                    ordered = copy
                                },
                            ) { Text("↑") }
                            TextButton(
                                enabled = index < ordered.lastIndex && !saving,
                                onClick = {
                                    val copy = ordered.toMutableList()
                                    val item = copy.removeAt(index)
                                    copy.add(index + 1, item)
                                    ordered = copy
                                },
                            ) { Text("↓") }
                        }
                    }
                }
            }
        },
        confirmButton = {
            Button(
                enabled = online && ordered.isNotEmpty() && !saving,
                onClick = {
                    scope.launch {
                        saving = true
                        try {
                            val result = viewModel.reorderChapters(
                                projectId,
                                ordered.map(ReplicaEntity::entityId),
                            )
                            viewModel.reportNotice(
                                canonicalWriteNotice("章节顺序已由 PC 端统一更新", result),
                            )
                            onDismiss()
                        } catch (error: Exception) {
                            viewModel.reportError(error.toUserFacingMessage())
                        } finally {
                            saving = false
                        }
                    }
                },
            ) {
                if (saving) CircularProgressIndicator(Modifier.height(18.dp).width(18.dp))
                else Text("保存顺序")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !saving) { Text("取消") } },
    )
}

@Composable
internal fun ChapterHistoryDialog(
    projectId: String,
    chapter: ReplicaEntity,
    online: Boolean,
    viewModel: MainViewModel,
    onDismiss: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var loading by remember { mutableStateOf(false) }
    var snapshots by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var detail by remember { mutableStateOf<JsonObject?>(null) }
    var diff by remember { mutableStateOf<JsonObject?>(null) }
    var restoreCandidate by remember { mutableStateOf<JsonObject?>(null) }
    var restoring by remember { mutableStateOf(false) }

    fun reload() {
        if (!online) return
        scope.launch {
            loading = true
            try {
                val data = viewModel.chapterSnapshots(projectId, chapter.entityId)
                snapshots = data.arrayObjects("items")
            } catch (error: Exception) {
                viewModel.reportError(error.toUserFacingMessage())
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(chapter.entityId, online) { reload() }

    AlertDialog(
        onDismissRequest = { if (!restoring) onDismiss() },
        title = { Text("${chapter.text("title").ifBlank { "章节" }} · 版本历史") },
        text = {
            Column(
                Modifier.heightIn(max = 580.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                if (!online) {
                    Text("版本历史、diff 和恢复属于 PC 领域命令，需要连接 Gateway。")
                } else if (loading && snapshots.isEmpty()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.height(20.dp).width(20.dp))
                        Spacer(Modifier.width(10.dp))
                        Text("正在读取 PC 章节快照…")
                    }
                }

                snapshots.forEachIndexed { index, snapshot ->
                    val snapshotId = snapshot.string("id")
                    OutlinedCard(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                            Text(
                                "v${snapshot.int("version_number")} · ${snapshot.int("word_count")} 字",
                                fontWeight = FontWeight.SemiBold,
                            )
                            Text(
                                "${snapshot.string("trigger_type")} · ${snapshot.string("created_at")}",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                TextButton(
                                    enabled = !restoring,
                                    onClick = {
                                        scope.launch {
                                            loading = true
                                            try {
                                                detail = viewModel.chapterSnapshot(
                                                    projectId,
                                                    chapter.entityId,
                                                    snapshotId,
                                                )
                                                diff = null
                                            } catch (error: Exception) {
                                                viewModel.reportError(error.toUserFacingMessage())
                                            } finally {
                                                loading = false
                                            }
                                        }
                                    },
                                ) { Text("查看") }
                                if (index < snapshots.lastIndex) {
                                    TextButton(
                                        enabled = !restoring,
                                        onClick = {
                                            val olderId = snapshots[index + 1].string("id")
                                            scope.launch {
                                                loading = true
                                                try {
                                                    diff = viewModel.chapterSnapshotDiff(
                                                        projectId,
                                                        chapter.entityId,
                                                        olderId,
                                                        snapshotId,
                                                    )
                                                    detail = null
                                                } catch (error: Exception) {
                                                    viewModel.reportError(error.toUserFacingMessage())
                                                } finally {
                                                    loading = false
                                                }
                                            }
                                        },
                                    ) { Text("与上一版对比") }
                                }
                                TextButton(
                                    onClick = { restoreCandidate = snapshot },
                                    enabled = !restoring,
                                ) { Text("恢复") }
                            }
                        }
                    }
                }

                detail?.let { snapshot ->
                    HorizontalDivider()
                    Text("快照正文", fontWeight = FontWeight.SemiBold)
                    Text(
                        snapshot.string("content").ifBlank { "（空正文）" },
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace,
                    )
                }
                diff?.let { result ->
                    HorizontalDivider()
                    Text(
                        "PC diff · ${result.int("total_changes")} 处变化",
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        formatPcSnapshotDiff(result),
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace,
                    )
                }
                if (snapshots.isEmpty() && !loading && online) {
                    Text("PC 尚未保存章节快照。")
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss, enabled = !restoring) { Text("关闭") }
        },
    )

    restoreCandidate?.let { snapshot ->
        AlertDialog(
            onDismissRequest = { if (!restoring) restoreCandidate = null },
            title = { Text("恢复到 v${snapshot.int("version_number")}") },
            text = {
                Text("PC 会创建新的 restore 版本、恢复对应 ledger checkpoint，并把受影响的旧治理结论标记为需要复检。")
            },
            confirmButton = {
                Button(
                    enabled = !restoring,
                    onClick = {
                        if (restoring) return@Button
                        restoring = true
                        loading = true
                        val snapshotId = snapshot.string("id")
                        scope.launch {
                            try {
                                val result = viewModel.restoreChapterSnapshot(
                                    projectId,
                                    chapter.entityId,
                                    snapshotId,
                                )
                                restoreCandidate = null
                                detail = null
                                diff = null
                                if (result.mobileRefreshWarning().isBlank()) reload()
                                viewModel.reportNotice(
                                    canonicalWriteNotice(
                                        "章节已通过 PC 版本系统恢复，并同步最新副本",
                                        result,
                                    ),
                                )
                            } catch (error: Exception) {
                                viewModel.reportError(error.toUserFacingMessage())
                            } finally {
                                restoring = false
                                loading = false
                            }
                        }
                    },
                ) {
                    if (restoring) {
                        CircularProgressIndicator(Modifier.height(18.dp).width(18.dp))
                    } else {
                        Text("确认恢复")
                    }
                }
            },
            dismissButton = {
                TextButton(
                    onClick = { restoreCandidate = null },
                    enabled = !restoring,
                ) { Text("取消") }
            },
        )
    }
}

private enum class CharacterAdvancedTab(val label: String) {
    Relationships("关系"),
    AiConfig("AI 配置"),
    Versions("版本"),
}

@Composable
internal fun CharacterAdvancedDialog(
    projectId: String,
    character: ReplicaEntity,
    online: Boolean,
    viewModel: MainViewModel,
    onDismiss: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var tab by remember { mutableStateOf(CharacterAdvancedTab.Relationships) }
    var loading by remember { mutableStateOf(false) }
    var nodes by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var relations by remember { mutableStateOf<List<PcEditableRelationship>>(emptyList()) }
    var aiConfig by remember { mutableStateOf<Map<String, String>>(emptyMap()) }
    var versions by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var versionDetail by remember { mutableStateOf<JsonObject?>(null) }

    fun loadAll() {
        if (!online) return
        scope.launch {
            loading = true
            try {
                val network = viewModel.characterRelationshipNetwork(projectId)
                nodes = network.arrayObjects("nodes")
                relations = pcEditableRelationships(network, character.entityId)
                aiConfig = aiConfigFields(viewModel.characterAiConfig(projectId, character.entityId))
                versions = viewModel.characterVersions(projectId, character.entityId).arrayObjects("items")
            } catch (error: Exception) {
                viewModel.reportError(error.toUserFacingMessage())
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(character.entityId, online) { loadAll() }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(character.text("name").ifBlank { "角色高级资料" }) },
        text = {
            Column(
                Modifier.heightIn(max = 620.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                if (!online) {
                    Text("关系网、角色 AI 配置和版本历史属于 PC 专用领域能力，需要连接 Gateway。")
                    return@Column
                }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    CharacterAdvancedTab.entries.forEach { item ->
                        AssistChip(
                            onClick = { tab = item },
                            label = { Text(item.label) },
                        )
                    }
                }
                if (loading && nodes.isEmpty() && versions.isEmpty()) {
                    CircularProgressIndicator()
                }
                when (tab) {
                    CharacterAdvancedTab.Relationships -> RelationshipEditor(
                        currentCharacterId = character.entityId,
                        currentCharacterName = character.text("name").ifBlank { "当前角色" },
                        nodes = nodes,
                        relations = relations,
                        onRelationsChanged = { relations = it },
                        onSave = {
                            scope.launch {
                                loading = true
                                try {
                                    val payload = JsonArray(relations.map(::pcRelationshipMutationPayload))
                                    val result = viewModel.replaceCharacterRelationships(
                                        projectId,
                                        character.entityId,
                                        payload,
                                    )
                                    viewModel.reportNotice(
                                        canonicalWriteNotice(
                                            "角色关系已由 PC 关系网接口统一保存",
                                            result,
                                        ),
                                    )
                                    if (result.mobileRefreshWarning().isBlank()) loadAll()
                                } catch (error: Exception) {
                                    viewModel.reportError(error.toUserFacingMessage())
                                } finally {
                                    loading = false
                                }
                            }
                        },
                    )
                    CharacterAdvancedTab.AiConfig -> AiConfigEditor(
                        values = aiConfig,
                        onChanged = { aiConfig = it },
                        onSave = {
                            scope.launch {
                                loading = true
                                try {
                                    val result = viewModel.updateCharacterAiConfig(
                                        projectId,
                                        character.entityId,
                                        aiConfigPayload(aiConfig),
                                    )
                                    viewModel.reportNotice(
                                        canonicalWriteNotice(
                                            "角色 AI 配置已通过 PC 专用接口保存",
                                            result,
                                        ),
                                    )
                                    if (result.mobileRefreshWarning().isBlank()) loadAll()
                                } catch (error: Exception) {
                                    viewModel.reportError(error.toUserFacingMessage())
                                } finally {
                                    loading = false
                                }
                            }
                        },
                    )
                    CharacterAdvancedTab.Versions -> CharacterVersionList(
                        versions = versions,
                        detail = versionDetail,
                        onOpen = { versionId ->
                            scope.launch {
                                loading = true
                                try {
                                    versionDetail = viewModel.characterVersion(
                                        projectId,
                                        character.entityId,
                                        versionId,
                                    )
                                } catch (error: Exception) {
                                    viewModel.reportError(error.toUserFacingMessage())
                                } finally {
                                    loading = false
                                }
                            }
                        },
                    )
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("关闭") } },
    )
}

private enum class WorldAdvancedTab(val label: String) {
    Versions("版本"),
    Timeline("时间线"),
}

@Composable
internal fun WorldAdvancedDialog(
    projectId: String,
    entry: ReplicaEntity,
    online: Boolean,
    viewModel: MainViewModel,
    onDismiss: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var tab by remember { mutableStateOf(WorldAdvancedTab.Versions) }
    var loading by remember { mutableStateOf(false) }
    var versions by remember { mutableStateOf<List<JsonObject>>(emptyList()) }
    var timeline by remember { mutableStateOf<List<JsonObject>>(emptyList()) }

    LaunchedEffect(entry.entityId, online) {
        if (!online) return@LaunchedEffect
        loading = true
        try {
            versions = viewModel.worldVersions(projectId, entry.entityId).arrayObjects("items")
            timeline = viewModel.worldTimeline(projectId, entry.entityId).arrayObjects("items")
        } catch (error: Exception) {
            viewModel.reportError(error.toUserFacingMessage())
        } finally {
            loading = false
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("${entry.text("title").ifBlank { "世界观条目" }} · 历史") },
        text = {
            Column(
                Modifier.heightIn(max = 600.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                if (!online) {
                    Text("世界观版本和时间线由 PC 维护，需要连接 Gateway 才能查看。")
                    return@Column
                }
                Text(
                    "世界观关系目前没有 PC 专用 HTTP 编辑路由，手机只保留同步数据，不自行发明写接口。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    WorldAdvancedTab.entries.forEach { item ->
                        AssistChip(onClick = { tab = item }, label = { Text(item.label) })
                    }
                }
                if (loading) CircularProgressIndicator()
                when (tab) {
                    WorldAdvancedTab.Versions -> {
                        if (versions.isEmpty() && !loading) Text("暂无世界观版本记录。")
                        versions.forEach { version ->
                            OutlinedCard(Modifier.fillMaxWidth()) {
                                Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                    Text("v${version.int("version_number")}", fontWeight = FontWeight.SemiBold)
                                    Text(version.string("change_summary").ifBlank { "无变更摘要" })
                                    version.string("source_chapter_id").takeIf(String::isNotBlank)?.let {
                                        Text("来源章节：$it", style = MaterialTheme.typography.bodySmall)
                                    }
                                    Text(version.string("created_at"), style = MaterialTheme.typography.labelSmall)
                                }
                            }
                        }
                    }
                    WorldAdvancedTab.Timeline -> {
                        if (timeline.isEmpty() && !loading) Text("暂无世界观时间线事件。")
                        timeline.forEach { event ->
                            OutlinedCard(Modifier.fillMaxWidth()) {
                                Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                    Text(
                                        "#${event.int("sort_order")} · ${event.string("event_type").ifBlank { "event" }}",
                                        fontWeight = FontWeight.SemiBold,
                                    )
                                    Text(event.string("event_description").ifBlank { "无事件描述" })
                                    event.string("evidence").takeIf(String::isNotBlank)?.let {
                                        Text("证据：$it", style = MaterialTheme.typography.bodySmall)
                                    }
                                    event.string("chapter_id").takeIf(String::isNotBlank)?.let {
                                        Text("章节：$it", style = MaterialTheme.typography.bodySmall)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("关闭") } },
    )
}

@Composable
private fun RelationshipEditor(
    currentCharacterId: String,
    currentCharacterName: String,
    nodes: List<JsonObject>,
    relations: List<PcEditableRelationship>,
    onRelationsChanged: (List<PcEditableRelationship>) -> Unit,
    onSave: () -> Unit,
) {
    val existingCounterparts = relations.mapTo(mutableSetOf()) {
        it.counterpartId(currentCharacterId)
    }
    val available = nodes.filter {
        it.string("id") != currentCharacterId && it.string("id") !in existingCounterparts
    }
    Text(
        "PC 的关系更新是“替换当前角色的全部关系”。手机会保留每条边的原始方向，避免从终点角色保存时反转语义。",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    if (available.isNotEmpty()) {
        Text("添加关系", fontWeight = FontWeight.SemiBold)
        available.take(12).forEach { node ->
            AssistChip(
                onClick = {
                    onRelationsChanged(
                        relations + pcNewRelationship(
                            currentCharacterId = currentCharacterId,
                            targetCharacterId = node.string("id"),
                            targetName = node.string("name"),
                        ),
                    )
                },
                label = { Text("＋ ${node.string("name")}") },
            )
        }
    }
    relations.forEachIndexed { index, relation ->
        OutlinedCard(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    relation.counterpartName.ifBlank {
                        relation.counterpartId(currentCharacterId)
                    },
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    relation.directionLabel(currentCharacterId)
                        .replace("当前角色", currentCharacterName),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                TextButton(onClick = {
                    onRelationsChanged(relations.filterIndexed { itemIndex, _ -> itemIndex != index })
                }) { Text("移除") }
                OutlinedTextField(
                    value = relation.relationshipType,
                    onValueChange = { value ->
                        onRelationsChanged(relations.replaceAt(index, relation.copy(relationshipType = value)))
                    },
                    label = { Text("关系类型") },
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = relation.description,
                    onValueChange = { value ->
                        onRelationsChanged(relations.replaceAt(index, relation.copy(description = value)))
                    },
                    label = { Text("关系描述") },
                    minLines = 2,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
    Button(onClick = onSave, modifier = Modifier.fillMaxWidth()) { Text("保存完整关系列表") }
}

@Composable
private fun AiConfigEditor(
    values: Map<String, String>,
    onChanged: (Map<String, String>) -> Unit,
    onSave: () -> Unit,
) {
    Text(
        "这组配置与 PC 角色扮演/语气配置一致，不会被错误注入普通章节写作。",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    listOf(
        "tone_style" to "语气风格",
        "catchphrases" to "口头禅（一行一个）",
        "verbosity" to "话量偏好（brief / moderate / verbose）",
        "emotion_tendency" to "情感倾向",
        "model_override" to "角色专用模型覆盖",
        "custom_system_prompt" to "额外系统提示",
    ).forEach { (key, label) ->
        OutlinedTextField(
            value = values[key].orEmpty(),
            onValueChange = { onChanged(values + (key to it)) },
            label = { Text(label) },
            minLines = if (key in setOf("catchphrases", "custom_system_prompt")) 2 else 1,
            modifier = Modifier.fillMaxWidth(),
        )
    }
    Button(onClick = onSave, modifier = Modifier.fillMaxWidth()) { Text("保存 AI 配置") }
}

@Composable
private fun CharacterVersionList(
    versions: List<JsonObject>,
    detail: JsonObject?,
    onOpen: (String) -> Unit,
) {
    if (versions.isEmpty()) Text("暂无角色版本快照。")
    versions.forEach { version ->
        OutlinedCard(Modifier.fillMaxWidth()) {
            Row(
                Modifier.fillMaxWidth().padding(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("v${version.int("version_number")}", fontWeight = FontWeight.SemiBold)
                    Text(
                        version.string("change_summary").ifBlank { "无变更摘要" },
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Text(version.string("created_at"), style = MaterialTheme.typography.labelSmall)
                }
                TextButton(onClick = { onOpen(version.string("id")) }) { Text("查看") }
            }
        }
    }
    detail?.let { version ->
        HorizontalDivider()
        Text("历史角色快照", fontWeight = FontWeight.SemiBold)
        val snapshot = version["snapshot_data"]
        Text(
            snapshot?.toString() ?: "{}",
            style = MaterialTheme.typography.bodySmall,
            fontFamily = FontFamily.Monospace,
        )
    }
}

private fun aiConfigFields(config: JsonObject): Map<String, String> = mapOf(
    "tone_style" to config.string("tone_style"),
    "catchphrases" to config.stringList("catchphrases").joinToString("\n"),
    "verbosity" to config.string("verbosity"),
    "emotion_tendency" to config.string("emotion_tendency"),
    "model_override" to config.string("model_override"),
    "custom_system_prompt" to config.string("custom_system_prompt"),
)

private fun aiConfigPayload(values: Map<String, String>): JsonObject = buildJsonObject {
    putNullableString("tone_style", values["tone_style"])
    put(
        "catchphrases",
        JsonArray(
            values["catchphrases"].orEmpty()
                .split('\n', ',', '，', '、')
                .map(String::trim)
                .filter(String::isNotBlank)
                .map(::JsonPrimitive),
        ),
    )
    putNullableString("verbosity", values["verbosity"])
    putNullableString("emotion_tendency", values["emotion_tendency"])
    putNullableString("model_override", values["model_override"])
    putNullableString("custom_system_prompt", values["custom_system_prompt"])
}

private fun kotlinx.serialization.json.JsonObjectBuilder.putNullableString(key: String, value: String?) {
    val clean = value.orEmpty().trim()
    put(key, if (clean.isBlank()) JsonNull else JsonPrimitive(clean))
}

private fun formatPcSnapshotDiff(result: JsonObject): String {
    val chunks = result.arrayObjects("changes")
        .filter { it.string("type") != "equal" }
        .take(30)
    if (chunks.isEmpty()) return "没有文本差异。"
    return chunks.joinToString("\n\n") { chunk ->
        buildString {
            append("[${chunk.string("type")}]\n")
            chunk.stringList("from_lines").take(8).forEach { append("- ").append(it).append('\n') }
            chunk.stringList("to_lines").take(8).forEach { append("+ ").append(it).append('\n') }
        }.trimEnd()
    }
}

private fun canonicalWriteNotice(success: String, result: JsonObject): String {
    val warning = result.mobileRefreshWarning()
    return if (warning.isBlank()) success else "$success；手机副本待刷新：$warning"
}

private fun <T> List<T>.replaceAt(index: Int, value: T): List<T> =
    toMutableList().also { it[index] = value }

private fun JsonObject.arrayObjects(name: String): List<JsonObject> =
    (get(name) as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }

private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

private fun JsonObject.int(name: String): Int =
    (get(name) as? JsonPrimitive)?.intOrNull ?: 0

private fun JsonObject.stringList(name: String): List<String> = when (val value = get(name)) {
    is JsonArray -> value.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
    is JsonPrimitive -> value.contentOrNull.orEmpty().split('\n').filter(String::isNotBlank)
    else -> emptyList()
}
