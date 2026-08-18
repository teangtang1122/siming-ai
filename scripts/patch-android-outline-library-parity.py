from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    file.write_text(text.replace(old, new), encoding="utf-8")


# Canonical PC paths: Android must use the same outline reorder endpoint.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/data/network/PcApiPaths.kt",
    '''    fun chapterReorder(projectId: String): String =
        "${authoringCollection(projectId, "chapter")}/reorder"

''',
    '''    fun chapterReorder(projectId: String): String =
        "${authoringCollection(projectId, "chapter")}/reorder"

    fun outlineReorder(projectId: String): String =
        "${authoringCollection(projectId, "outline")}/reorder"

''',
)

# Gateway canonical commands for project deletion and outline reorder.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/data/network/GatewayApi.kt",
    "import kotlinx.serialization.json.JsonElement\n",
    "import kotlinx.serialization.json.JsonElement\nimport kotlinx.serialization.json.JsonNull\n",
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/data/network/GatewayApi.kt",
    '''    suspend fun deleteAuthoringEntity(
        connection: GatewayConnection,
        projectId: String,
        entityType: String,
        entityId: String,
    ) {
        request<ApiEnvelope<JsonElement>>(
            connection.baseUrl,
            PcApiPaths.authoringItem(projectId, entityType, entityId),
            "DELETE",
        )
    }

''',
    '''    suspend fun deleteAuthoringEntity(
        connection: GatewayConnection,
        projectId: String,
        entityType: String,
        entityId: String,
    ) {
        request<ApiEnvelope<JsonElement>>(
            connection.baseUrl,
            PcApiPaths.authoringItem(projectId, entityType, entityId),
            "DELETE",
        )
    }

    suspend fun deleteProject(connection: GatewayConnection, projectId: String) {
        request<ApiEnvelope<JsonElement>>(
            connection.baseUrl,
            PcApiPaths.project(projectId),
            "DELETE",
        )
    }

    suspend fun reorderOutline(
        connection: GatewayConnection,
        projectId: String,
        parentId: String?,
        nodeIds: List<String>,
    ): JsonObject = canonicalWrite(
        connection = connection,
        path = PcApiPaths.outlineReorder(projectId),
        method = "PUT",
        payload = buildJsonObject {
            put(
                "items",
                JsonArray(
                    nodeIds.mapIndexed { index, nodeId ->
                        buildJsonObject {
                            put("id", nodeId)
                            put("parent_id", parentId?.let(::JsonPrimitive) ?: JsonNull)
                            put("sort_order", index)
                        }
                    },
                ),
            )
        },
    )

''',
)

# Room already supports project replica deletion; add project-scoped outbox/conflict cleanup.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/data/local/SimingDatabase.kt",
    '''    @Query("DELETE FROM replica_entities WHERE projectId = :projectId")
    suspend fun deleteProjectReplica(projectId: String)

''',
    '''    @Query("DELETE FROM replica_entities WHERE projectId = :projectId")
    suspend fun deleteProjectReplica(projectId: String)

    @Query("DELETE FROM sync_outbox WHERE projectId = :projectId")
    suspend fun deleteProjectMutations(projectId: String)

    @Query("DELETE FROM local_conflicts WHERE projectId = :projectId")
    suspend fun deleteProjectConflicts(projectId: String)

''',
)

# Repository: safe whole-project deletion and outline reorder with offline replay.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/data/SimingRepository.kt",
    '''    suspend fun deleteEntity(projectId: String, entityType: String, entityId: String) {
        require(entityType != "project") { "移动端不会直接删除整部作品" }
''',
    '''    suspend fun deleteProject(projectId: String) = canonicalCommandMutex.withLock {
        val key = ReplicaEntity.key(projectId, "project", projectId)
        val current = dao.entity(key) ?: return@withLock
        require(!current.conflicted) { "请先处理这部作品的版本分岔，再执行删除" }

        val connection = dao.connection()
        if (connection != null) {
            val canonicalReady = prepareCanonicalWrite()
            if (canonicalReady) {
                try {
                    api.deleteProject(connection, projectId)
                    purgeLocalProject(projectId)
                    return@withLock
                } catch (error: GatewayHttpException) {
                    throw error
                } catch (_: IOException) {
                    // A canonical project must not be converted into a local-only
                    // delete when the PC is unreachable: it would be resurrected
                    // on the next authoritative pull.
                }
            }
        }

        check(isUnsyncedLocalProject(current)) {
            "这部作品已经进入 PC 权威库；请连接 PC Gateway 后再删除，避免下次同步把作品重新拉回手机"
        }
        purgeLocalProject(projectId)
    }

    private suspend fun isUnsyncedLocalProject(project: ReplicaEntity): Boolean {
        val pending = dao.pendingMutation(project.projectId, "project", project.projectId)
        return project.dirty &&
            project.revision == 0L &&
            pending?.operation == "upsert" &&
            pending.baseRevision == 0L
    }

    private suspend fun purgeLocalProject(projectId: String) {
        database.withTransaction {
            dao.deleteProjectMutations(projectId)
            dao.deleteProjectConflicts(projectId)
            dao.deleteProjectReplica(projectId)
        }
    }

    suspend fun deleteEntity(projectId: String, entityType: String, entityId: String) {
        require(entityType != "project") { "整部作品请使用作品库的删除操作" }
''',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/data/SimingRepository.kt",
    '''    suspend fun reorderChapters(projectId: String, chapterIds: List<String>): JsonObject =
        canonicalCommandMutex.withLock {
            val connection = canonicalCommandConnection()
            val result = api.reorderChapters(connection, projectId, chapterIds)
            refreshAfterCanonicalWrite(connection, projectId, result)
        }

''',
    '''    suspend fun reorderChapters(projectId: String, chapterIds: List<String>): JsonObject =
        canonicalCommandMutex.withLock {
            val connection = canonicalCommandConnection()
            val result = api.reorderChapters(connection, projectId, chapterIds)
            refreshAfterCanonicalWrite(connection, projectId, result)
        }

    suspend fun reorderOutline(
        projectId: String,
        parentId: String?,
        nodeIds: List<String>,
    ): JsonObject = canonicalCommandMutex.withLock {
        require(nodeIds.distinct().size == nodeIds.size) { "大纲排序包含重复节点" }
        val connection = dao.connection()
        if (connection != null && prepareCanonicalWrite()) {
            try {
                val result = api.reorderOutline(connection, projectId, parentId, nodeIds)
                return@withLock refreshAfterCanonicalWrite(connection, projectId, result)
            } catch (error: GatewayHttpException) {
                throw error
            } catch (_: IOException) {
                // Reordering is replay-safe because each outline node already
                // carries parent_id + sort_order in the canonical mutation.
            }
        }
        reorderOutlineOffline(projectId, parentId, nodeIds)
    }

    private suspend fun reorderOutlineOffline(
        projectId: String,
        parentId: String?,
        nodeIds: List<String>,
    ): JsonObject {
        nodeIds.forEachIndexed { index, nodeId ->
            val key = ReplicaEntity.key(projectId, "outline", nodeId)
            val current = dao.entity(key) ?: error("大纲节点不存在：$nodeId")
            require(!current.conflicted) { "请先处理大纲节点的版本分岔，再调整顺序" }
            val payload = current.payloadJson
                ?.let(json::parseToJsonElement)
                as? JsonObject
                ?: error("大纲节点缺少结构化数据")
            val actualParent = (payload["parent_id"] as? JsonPrimitive)?.contentOrNull
                ?.takeIf { it.isNotBlank() }
            require(actualParent == parentId) { "只能调整同一父节点下的大纲顺序" }
            val reordered = JsonObject(
                payload.toMutableMap().apply {
                    put("sort_order", JsonPrimitive(index))
                },
            )
            saveOfflineEntity(projectId, "outline", nodeId, reordered)
        }
        return buildJsonObject {
            put("mode", "offline_replay")
            put("parent_id", parentId?.let(::JsonPrimitive) ?: JsonNull)
            put("ids", JsonArray(nodeIds.map(::JsonPrimitive)))
        }
    }

''',
)

# ViewModel exposes the two user-facing operations.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/MainViewModel.kt",
    '''    suspend fun reorderChapters(projectId: String, chapterIds: List<String>): JsonObject =
        repository.reorderChapters(projectId, chapterIds)

''',
    '''    suspend fun reorderChapters(projectId: String, chapterIds: List<String>): JsonObject =
        repository.reorderChapters(projectId, chapterIds)

    fun reorderOutline(projectId: String, parentId: String?, nodeIds: List<String>) {
        viewModelScope.launch {
            try {
                repository.reorderOutline(projectId, parentId, nodeIds)
                uiState.value = uiState.value.copy(
                    notice = if (connection.value != null) {
                        "大纲顺序已通过 PC 端同一排序 API 更新"
                    } else {
                        "大纲顺序已保存到手机，恢复连接后按节点修订同步"
                    },
                )
            } catch (error: Exception) {
                showError(error)
            }
        }
    }

''',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/MainViewModel.kt",
    '''    fun importNovel(fileName: String, content: String, onCreated: (String) -> Unit) {
''',
    '''    fun deleteProject(projectId: String, onDeleted: () -> Unit) {
        viewModelScope.launch {
            try {
                repository.deleteProject(projectId)
                uiState.value = uiState.value.copy(
                    notice = if (connection.value != null) {
                        "作品已从 PC 权威库删除，手机副本已清理"
                    } else {
                        "尚未同步的本地作品已从手机删除"
                    },
                )
                onDeleted()
            } catch (error: Exception) {
                showError(error)
            }
        }
    }

    fun importNovel(fileName: String, content: String, onCreated: (String) -> Unit) {
''',
)

# Library deletion UI and special outline tree surface.
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '''    var showCreate by rememberSaveable { mutableStateOf(false) }
    Column(modifier.fillMaxSize()) {
''',
    '''    var showCreate by rememberSaveable { mutableStateOf(false) }
    var deleteTarget by remember { mutableStateOf<ReplicaEntity?>(null) }
    Column(modifier.fillMaxSize()) {
''',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '''                    ProjectCard(
                        project,
                        localOnly = connection == null,
                        onClick = { onOpenProject(project.projectId) },
                    )
''',
    '''                    ProjectCard(
                        project,
                        localOnly = connection == null,
                        onClick = { onOpenProject(project.projectId) },
                        onDelete = { deleteTarget = project },
                    )
''',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '''    if (showCreate) {
        CreateProjectDialog(
            onDismiss = { showCreate = false },
            onCreate = { title, description ->
                showCreate = false
                viewModel.createProject(title, description, onOpenProject)
            },
        )
    }
}

@Composable
private fun ProjectCard(project: ReplicaEntity, localOnly: Boolean, onClick: () -> Unit) {
''',
    '''    if (showCreate) {
        CreateProjectDialog(
            onDismiss = { showCreate = false },
            onCreate = { title, description ->
                showCreate = false
                viewModel.createProject(title, description, onOpenProject)
            },
        )
    }
    deleteTarget?.let { target ->
        val title = target.text("title").ifBlank { "未命名作品" }
        val canAttemptDelete = connection != null || (target.dirty && target.revision == 0L)
        AlertDialog(
            onDismissRequest = { deleteTarget = null },
            title = { Text("删除《$title》？") },
            text = {
                Text(
                    when {
                        connection != null -> "删除后会从 PC 权威作品库移除，并清理这台手机的离线副本。此操作不可撤销。"
                        canAttemptDelete -> "这部作品尚未同步到 PC，将只从当前手机移除。此操作不可撤销。"
                        else -> "这部作品已经与 PC 同步。为避免下次同步重新出现，请先连接 PC Gateway，再执行删除。"
                    },
                )
            },
            confirmButton = {
                TextButton(
                    enabled = canAttemptDelete,
                    onClick = {
                        viewModel.deleteProject(target.projectId) { deleteTarget = null }
                    },
                ) {
                    Text(if (canAttemptDelete) "确认删除" else "删除需联网")
                }
            },
            dismissButton = {
                TextButton(onClick = { deleteTarget = null }) { Text("取消") }
            },
        )
    }
}

@Composable
private fun ProjectCard(
    project: ReplicaEntity,
    localOnly: Boolean,
    onClick: () -> Unit,
    onDelete: () -> Unit,
) {
''',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '''            Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, Modifier.size(18.dp))
''',
    '''            IconButton(onClick = onDelete) {
                Icon(
                    Icons.Outlined.DeleteOutline,
                    "删除作品",
                    tint = MaterialTheme.colorScheme.error,
                    modifier = Modifier.size(20.dp),
                )
            }
            Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, Modifier.size(18.dp))
''',
)
replace_once(
    "mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt",
    '''            if (section == "assistant") {
                AssistantScreen(project.projectId, viewModel)
            } else {
                RecordList(
                    section = requireNotNull(currentSection),
                    records = records,
                    online = connection != null,
                    onOpen = { editor = EditorTarget(section, it) },
                    onAdvanced = if (section in setOf("chapter", "character", "world")) {
                        { record -> advanced = EditorTarget(section, record) }
                    } else {
                        null
                    },
                    onManageChapterOrder = if (section == "chapter") {
                        { showChapterOrder = true }
                    } else {
                        null
                    },
                )
            }
''',
    '''            when (section) {
                "assistant" -> AssistantScreen(project.projectId, viewModel)
                "outline" -> OutlineTreeList(
                    projectId = project.projectId,
                    records = records,
                    online = connection != null,
                    onOpen = { editor = EditorTarget("outline", it) },
                    onReorder = { parentId, nodeIds ->
                        viewModel.reorderOutline(project.projectId, parentId, nodeIds)
                    },
                )
                else -> RecordList(
                    section = requireNotNull(currentSection),
                    records = records,
                    online = connection != null,
                    onOpen = { editor = EditorTarget(section, it) },
                    onAdvanced = if (section in setOf("chapter", "character", "world")) {
                        { record -> advanced = EditorTarget(section, record) }
                    } else {
                        null
                    },
                    onManageChapterOrder = if (section == "chapter") {
                        { showChapterOrder = true }
                    } else {
                        null
                    },
                )
            }
''',
)

# Dedicated mobile outline UI keeps SimingApp from growing further.
outline_tree = r'''package com.siming.mobile.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowRight
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.material.icons.outlined.Reorder
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.siming.mobile.data.local.ReplicaEntity
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

private val outlineTreeJson = Json { ignoreUnknownKeys = true; explicitNulls = false }

internal data class OutlineTreeNode(
    val record: ReplicaEntity,
    val parentId: String?,
    val nodeType: String,
    val title: String,
    val summary: String,
    val sortOrder: Int,
    val children: List<OutlineTreeNode>,
)

private data class OutlineNodeMeta(
    val record: ReplicaEntity,
    val parentId: String?,
    val nodeType: String,
    val title: String,
    val summary: String,
    val sortOrder: Int,
)

private data class OutlineRenderRow(
    val node: OutlineTreeNode,
    val depth: Int,
    val siblings: List<OutlineTreeNode>,
    val index: Int,
)

internal fun buildOutlineTree(records: List<ReplicaEntity>): List<OutlineTreeNode> {
    val metas = records.map { record ->
        val payload = runCatching {
            record.payloadJson?.let(outlineTreeJson::parseToJsonElement) as? JsonObject
        }.getOrNull() ?: JsonObject(emptyMap())
        val parentId = (payload["parent_id"] as? JsonPrimitive)?.contentOrNull
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
        OutlineNodeMeta(
            record = record,
            parentId = parentId,
            nodeType = (payload["node_type"] as? JsonPrimitive)?.contentOrNull
                ?.takeIf { it in setOf("volume", "chapter", "section") }
                ?: "chapter",
            title = (payload["title"] as? JsonPrimitive)?.contentOrNull
                ?.trim()
                .orEmpty()
                .ifBlank { "未命名大纲节点" },
            summary = (payload["summary"] as? JsonPrimitive)?.contentOrNull.orEmpty(),
            sortOrder = (payload["sort_order"] as? JsonPrimitive)?.intOrNull ?: 0,
        )
    }
    val ids = metas.mapTo(mutableSetOf()) { it.record.entityId }
    val normalized = metas.map { meta ->
        if (meta.parentId != null && meta.parentId !in ids) meta.copy(parentId = null) else meta
    }
    val grouped = normalized.groupBy { it.parentId }
    val comparator = compareBy<OutlineNodeMeta>({ it.sortOrder }, { it.title }, { it.record.entityId })

    fun build(parentId: String?, ancestors: Set<String>): List<OutlineTreeNode> =
        grouped[parentId].orEmpty()
            .sortedWith(comparator)
            .filterNot { it.record.entityId in ancestors }
            .map { meta ->
                val nextAncestors = ancestors + meta.record.entityId
                OutlineTreeNode(
                    record = meta.record,
                    parentId = meta.parentId,
                    nodeType = meta.nodeType,
                    title = meta.title,
                    summary = meta.summary,
                    sortOrder = meta.sortOrder,
                    children = build(meta.record.entityId, nextAncestors),
                )
            }

    return build(null, emptySet())
}

internal fun moveOutlineSiblingIds(
    siblings: List<OutlineTreeNode>,
    nodeId: String,
    delta: Int,
): List<String> {
    val ids = siblings.map { it.record.entityId }.toMutableList()
    val from = ids.indexOf(nodeId)
    if (from < 0) return ids
    val to = (from + delta).coerceIn(0, ids.lastIndex)
    if (from == to) return ids
    val moved = ids.removeAt(from)
    ids.add(to, moved)
    return ids
}

private fun expandableIds(nodes: List<OutlineTreeNode>): Set<String> = buildSet {
    fun collect(items: List<OutlineTreeNode>) {
        items.forEach { node ->
            if (node.children.isNotEmpty()) add(node.record.entityId)
            collect(node.children)
        }
    }
    collect(nodes)
}

private fun renderRows(
    roots: List<OutlineTreeNode>,
    expanded: Map<String, Boolean>,
): List<OutlineRenderRow> = buildList {
    fun append(nodes: List<OutlineTreeNode>, depth: Int) {
        nodes.forEachIndexed { index, node ->
            add(OutlineRenderRow(node, depth, nodes, index))
            if (expanded[node.record.entityId] == true) append(node.children, depth + 1)
        }
    }
    append(roots, 0)
}

@Composable
internal fun OutlineTreeList(
    projectId: String,
    records: List<ReplicaEntity>,
    online: Boolean,
    onOpen: (ReplicaEntity) -> Unit,
    onReorder: (String?, List<String>) -> Unit,
) {
    val roots = remember(records) { buildOutlineTree(records) }
    val expanded = remember(projectId) { mutableStateMapOf<String, Boolean>() }
    var ordering by rememberSaveable(projectId) { mutableStateOf(false) }
    val allExpandable = remember(roots) { expandableIds(roots) }
    val rows = renderRows(roots, expanded)

    LazyColumn(
        contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 96.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("大纲", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
                Text(
                    if (online) {
                        "按卷 → 章 → 节查看；同级排序直接调用 PC 的大纲排序 API。"
                    } else {
                        "按卷 → 章 → 节查看；离线排序会保存为可回放的节点修订，联网后同步。"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(
                        onClick = {
                            val expand = allExpandable.any { expanded[it] != true }
                            allExpandable.forEach { expanded[it] = expand }
                        },
                        enabled = allExpandable.isNotEmpty(),
                    ) {
                        Text(if (allExpandable.any { expanded[it] != true }) "全部展开" else "全部收起")
                    }
                    OutlinedButton(onClick = { ordering = !ordering }, enabled = records.size > 1) {
                        Icon(Icons.Outlined.Reorder, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(if (ordering) "完成排序" else "调整顺序")
                    }
                }
            }
        }

        if (records.isEmpty()) {
            item {
                OutlinedCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("还没有大纲节点", fontWeight = FontWeight.SemiBold)
                        Text(
                            "点击右下角“＋”创建卷、章或节。",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        } else {
            items(rows, key = { it.node.record.key }) { row ->
                OutlineTreeRow(
                    row = row,
                    expanded = expanded[row.node.record.entityId] == true,
                    ordering = ordering,
                    onToggle = {
                        expanded[row.node.record.entityId] =
                            expanded[row.node.record.entityId] != true
                    },
                    onOpen = { onOpen(row.node.record) },
                    onMove = { delta ->
                        val ids = moveOutlineSiblingIds(row.siblings, row.node.record.entityId, delta)
                        onReorder(row.node.parentId, ids)
                    },
                )
            }
        }
    }
}

@Composable
private fun OutlineTreeRow(
    row: OutlineRenderRow,
    expanded: Boolean,
    ordering: Boolean,
    onToggle: () -> Unit,
    onOpen: () -> Unit,
    onMove: (Int) -> Unit,
) {
    val node = row.node
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = (row.depth * 18).dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedCard(Modifier.weight(1f)) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 9.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (node.children.isNotEmpty()) {
                    IconButton(onClick = onToggle) {
                        Icon(
                            if (expanded) Icons.Outlined.ExpandMore else Icons.Outlined.KeyboardArrowRight,
                            if (expanded) "收起${node.title}" else "展开${node.title}",
                        )
                    }
                } else {
                    Box(Modifier.size(48.dp))
                }
                Surface(
                    shape = RoundedCornerShape(6.dp),
                    color = MaterialTheme.colorScheme.secondaryContainer,
                ) {
                    Text(
                        when (node.nodeType) {
                            "volume" -> "卷"
                            "section" -> "节"
                            else -> "章"
                        },
                        modifier = Modifier.padding(horizontal = 7.dp, vertical = 3.dp),
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Bold,
                    )
                }
                Spacer(Modifier.width(9.dp))
                Column(
                    Modifier.weight(1f).clickable(onClick = onOpen),
                    verticalArrangement = Arrangement.spacedBy(3.dp),
                ) {
                    Text(node.title, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    if (node.summary.isNotBlank()) {
                        Text(
                            node.summary,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    Text(
                        when {
                            node.record.conflicted -> "有版本分岔"
                            node.record.dirty -> "本机有新修改"
                            else -> "修订 ${node.record.revision}"
                        },
                        style = MaterialTheme.typography.labelSmall,
                        color = if (node.record.conflicted) {
                            MaterialTheme.colorScheme.error
                        } else {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        },
                    )
                }
                TextButton(onClick = onOpen) {
                    Icon(Icons.Outlined.Edit, null, Modifier.size(17.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("编辑")
                }
            }
        }
        if (ordering) {
            Column {
                IconButton(onClick = { onMove(-1) }, enabled = row.index > 0) {
                    Icon(Icons.Outlined.KeyboardArrowUp, "上移${node.title}")
                }
                IconButton(onClick = { onMove(1) }, enabled = row.index < row.siblings.lastIndex) {
                    Icon(Icons.Outlined.KeyboardArrowDown, "下移${node.title}")
                }
            }
        }
    }
}
'''
Path("mobile/android/app/src/main/java/com/siming/mobile/ui/OutlineTree.kt").write_text(outline_tree, encoding="utf-8")

# Pure model tests for the hierarchy and sibling reordering.
outline_test = r'''package com.siming.mobile.ui

import com.siming.mobile.data.local.ReplicaEntity
import kotlin.test.Test
import kotlin.test.assertEquals

class OutlineTreeModelTest {
    private fun record(id: String, payload: String) = ReplicaEntity(
        key = ReplicaEntity.key("p1", "outline", id),
        projectId = "p1",
        entityType = "outline",
        entityId = id,
        revision = 1,
        operation = "upsert",
        payloadJson = payload,
        contentHash = "hash-$id",
        serverModifiedAt = "2026-08-18T00:00:00Z",
    )

    @Test
    fun `builds volume chapter section hierarchy by parent and sort order`() {
        val tree = buildOutlineTree(
            listOf(
                record("s1", """{"title":"第一节","node_type":"section","parent_id":"c1","sort_order":0}"""),
                record("c2", """{"title":"第二章","node_type":"chapter","parent_id":"v1","sort_order":1}"""),
                record("v1", """{"title":"第一卷","node_type":"volume","sort_order":0}"""),
                record("c1", """{"title":"第一章","node_type":"chapter","parent_id":"v1","sort_order":0}"""),
            ),
        )

        assertEquals(listOf("v1"), tree.map { it.record.entityId })
        assertEquals(listOf("c1", "c2"), tree.single().children.map { it.record.entityId })
        assertEquals(listOf("s1"), tree.single().children.first().children.map { it.record.entityId })
    }

    @Test
    fun `moves only inside the current sibling group`() {
        val siblings = buildOutlineTree(
            listOf(
                record("a", """{"title":"A","node_type":"volume","sort_order":0}"""),
                record("b", """{"title":"B","node_type":"volume","sort_order":1}"""),
                record("c", """{"title":"C","node_type":"volume","sort_order":2}"""),
            ),
        )

        assertEquals(listOf("b", "a", "c"), moveOutlineSiblingIds(siblings, "a", 1))
        assertEquals(listOf("a", "c", "b"), moveOutlineSiblingIds(siblings, "c", -1))
    }
}
'''
Path("mobile/android/app/src/test/java/com/siming/mobile/ui/OutlineTreeModelTest.kt").write_text(outline_test, encoding="utf-8")

# Path regression test.
replace_once(
    "mobile/android/app/src/test/java/com/siming/mobile/data/network/PcApiPathsTest.kt",
    '''    @Test
    fun `creation path parameters reject path injection`() {
''',
    '''    @Test
    fun `project and outline management use canonical PC routes`() {
        assertEquals("/api/v1/projects/project-1", PcApiPaths.project("project-1"))
        assertEquals(
            "/api/v1/projects/project-1/outline/reorder",
            PcApiPaths.outlineReorder("project-1"),
        )
    }

    @Test
    fun `creation path parameters reject path injection`() {
''',
)

# Make the parity contract honest about these mobile capabilities.
contract = Path("contracts/mobile-pc-parity.json")
text = contract.read_text(encoding="utf-8")
old = '        "project": "authoring.project",\n        "chapterReorder": "chapter.reorder",'
new = '        "project": "authoring.project",\n        "outlineReorder": "authoring.outline",\n        "chapterReorder": "chapter.reorder",'
if text.count(old) != 1:
    raise SystemExit("mobile-pc-parity.json: pc_api_paths symbol anchor mismatch")
text = text.replace(old, new)
if text.count('"summary": "作品资料读取与更新"') != 1:
    raise SystemExit("mobile-pc-parity.json: project summary anchor mismatch")
text = text.replace('"summary": "作品资料读取与更新"', '"summary": "作品创建、读取、更新和删除"')
if text.count('"summary": "大纲节点创建、读取和更新"') != 1:
    raise SystemExit("mobile-pc-parity.json: outline summary anchor mismatch")
text = text.replace(
    '"summary": "大纲节点创建、读取和更新"',
    '"summary": "大纲树创建、读取、更新、删除和同级排序"',
)
contract.write_text(text, encoding="utf-8")
