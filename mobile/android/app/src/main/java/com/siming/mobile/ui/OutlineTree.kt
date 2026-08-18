package com.siming.mobile.ui

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
