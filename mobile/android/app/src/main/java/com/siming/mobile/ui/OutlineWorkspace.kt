package com.siming.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material.icons.outlined.Reorder
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.siming.mobile.data.local.ReplicaEntity
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject

internal data class OutlineEditorTarget(
    val record: ReplicaEntity?,
    val suggestedParentId: String? = null,
)

private data class MobileOutlineRow(
    val node: OutlineTreeNode,
    val depth: Int,
    val siblings: List<OutlineTreeNode>,
    val index: Int,
)

private val outlineEditorJson = Json { ignoreUnknownKeys = true; explicitNulls = false }

internal fun outlineStatusLabel(raw: String): String = when (raw) {
    "in_progress" -> "进行中"
    "completed" -> "已完成"
    else -> "待写"
}

internal fun outlineTypeLabel(raw: String): String = when (raw) {
    "volume" -> "卷"
    "section" -> "节"
    else -> "章"
}

internal fun outlineSuggestedChildType(parentType: String?): String = when (parentType) {
    "volume" -> "chapter"
    "chapter" -> "section"
    else -> "chapter"
}

private fun outlineParentId(record: ReplicaEntity?): String? =
    record?.formText("parent_id")?.trim()?.takeIf(String::isNotEmpty)

internal fun nextOutlineSortOrder(
    records: List<ReplicaEntity>,
    parentId: String?,
    currentId: String? = null,
): Int = records
    .asSequence()
    .filter { it.entityId != currentId && outlineParentId(it) == parentId }
    .mapNotNull { it.formText("sort_order").toIntOrNull() }
    .maxOrNull()
    ?.plus(1)
    ?: 0

internal fun outlineSortOrderForSave(
    record: ReplicaEntity?,
    records: List<ReplicaEntity>,
    parentId: String?,
): Int {
    if (record != null && outlineParentId(record) == parentId) {
        return record.formText("sort_order").toIntOrNull() ?: 0
    }
    return nextOutlineSortOrder(records, parentId, record?.entityId)
}

internal fun validOutlineParentType(nodeType: String, parentType: String?): Boolean = when (nodeType) {
    "volume" -> parentType == null
    "section" -> parentType == "chapter"
    else -> parentType == null || parentType == "volume"
}

private fun blockedOutlineParentIds(records: List<ReplicaEntity>, currentId: String?): Set<String> {
    if (currentId == null) return emptySet()
    val childrenByParent = records.groupBy { it.formText("parent_id").trim().takeIf(String::isNotEmpty) }
    val blocked = mutableSetOf(currentId)
    fun collect(parentId: String) {
        childrenByParent[parentId].orEmpty().forEach { child ->
            if (blocked.add(child.entityId)) collect(child.entityId)
        }
    }
    collect(currentId)
    return blocked
}

internal fun outlineParentOptions(
    records: List<ReplicaEntity>,
    currentId: String?,
    nodeType: String,
): List<ReplicaEntity> {
    val blocked = blockedOutlineParentIds(records, currentId)
    return records.filter { candidate ->
        candidate.entityId !in blocked && validOutlineParentType(nodeType, candidate.formText("node_type").ifBlank { "chapter" })
    }
}

private fun mobileOutlineRows(
    roots: List<OutlineTreeNode>,
    expanded: Map<String, Boolean>,
): List<MobileOutlineRow> = buildList {
    fun append(nodes: List<OutlineTreeNode>, depth: Int) {
        nodes.forEachIndexed { index, node ->
            add(MobileOutlineRow(node, depth, nodes, index))
            if (expanded[node.record.entityId] == true) append(node.children, depth + 1)
        }
    }
    append(roots, 0)
}

private fun expandableOutlineIds(nodes: List<OutlineTreeNode>): Set<String> = buildSet {
    fun collect(items: List<OutlineTreeNode>) {
        items.forEach { node ->
            if (node.children.isNotEmpty()) add(node.record.entityId)
            collect(node.children)
        }
    }
    collect(nodes)
}

@Composable
internal fun MobileOutlineWorkspace(
    projectId: String,
    records: List<ReplicaEntity>,
    online: Boolean,
    onOpen: (ReplicaEntity) -> Unit,
    onAddChild: (ReplicaEntity) -> Unit,
    onReorder: (String?, List<String>) -> Unit,
) {
    val roots = remember(records) { buildOutlineTree(records) }
    val expanded = remember(projectId) { mutableStateMapOf<String, Boolean>() }
    var ordering by rememberSaveable(projectId) { mutableStateOf(false) }
    val expandableIds = remember(roots) { expandableOutlineIds(roots) }
    val rows = mobileOutlineRows(roots, expanded)
    val volumes = records.count { it.formText("node_type") == "volume" }
    val chapters = records.count { it.formText("node_type").ifBlank { "chapter" } == "chapter" }
    val sections = records.count { it.formText("node_type") == "section" }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 104.dp),
        verticalArrangement = Arrangement.spacedBy(9.dp),
    ) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                ScreenHeading(
                    kicker = "",
                    title = "故事结构",
                    detail = if (online) {
                        "按卷、章、节整理剧情；排序直接使用 PC 的 canonical 大纲顺序。"
                    } else {
                        "离线也能整理结构；修改会进入可靠同步队列。"
                    },
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    MicroTag("$volumes 卷", SimingCinnabar)
                    MicroTag("$chapters 章", SimingBlue)
                    MicroTag("$sections 节", SimingGreen)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(
                        onClick = {
                            val shouldExpand = expandableIds.any { expanded[it] != true }
                            expandableIds.forEach { expanded[it] = shouldExpand }
                        },
                        enabled = expandableIds.isNotEmpty(),
                    ) {
                        Text(if (expandableIds.any { expanded[it] != true }) "全部展开" else "全部收起")
                    }
                    OutlinedButton(onClick = { ordering = !ordering }, enabled = records.size > 1) {
                        Icon(Icons.Outlined.Reorder, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(if (ordering) "完成" else "排序")
                    }
                }
            }
        }

        if (records.isEmpty()) {
            item {
                EmptyPanel(
                    Icons.Outlined.Reorder,
                    "还没有故事结构",
                    "点击右下角“＋”创建卷、章或节。",
                )
            }
        } else {
            items(rows, key = { it.node.record.key }) { row ->
                MobileOutlineCard(
                    row = row,
                    expanded = expanded[row.node.record.entityId] == true,
                    ordering = ordering,
                    onToggle = {
                        expanded[row.node.record.entityId] = expanded[row.node.record.entityId] != true
                    },
                    onOpen = { onOpen(row.node.record) },
                    onAddChild = { onAddChild(row.node.record) },
                    onMove = { delta ->
                        onReorder(
                            row.node.parentId,
                            moveOutlineSiblingIds(row.siblings, row.node.record.entityId, delta),
                        )
                    },
                )
            }
        }
    }
}

@Composable
private fun MobileOutlineCard(
    row: MobileOutlineRow,
    expanded: Boolean,
    ordering: Boolean,
    onToggle: () -> Unit,
    onOpen: () -> Unit,
    onAddChild: () -> Unit,
    onMove: (Int) -> Unit,
) {
    val node = row.node
    Row(
        modifier = Modifier.fillMaxWidth().padding(start = (row.depth * 16).dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedCard(modifier = Modifier.weight(1f)) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (node.children.isNotEmpty()) {
                    IconButton(onClick = onToggle) {
                        Icon(
                            if (expanded) Icons.Outlined.ExpandMore else Icons.AutoMirrored.Outlined.KeyboardArrowRight,
                            if (expanded) "收起${node.title}" else "展开${node.title}",
                        )
                    }
                } else {
                    Spacer(Modifier.width(48.dp))
                }
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = when (node.nodeType) {
                        "volume" -> MaterialTheme.colorScheme.primaryContainer
                        "section" -> MaterialTheme.colorScheme.tertiaryContainer
                        else -> MaterialTheme.colorScheme.secondaryContainer
                    },
                ) {
                    Text(
                        outlineTypeLabel(node.nodeType),
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Bold,
                    )
                }
                Spacer(Modifier.width(10.dp))
                Column(
                    modifier = Modifier.weight(1f).clickable(onClick = onOpen),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
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
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            outlineStatusLabel(node.record.formText("status")),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        if (node.children.isNotEmpty()) {
                            Text("${node.children.size} 个子节点", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        when {
                            node.record.conflicted -> MicroTag("有分岔", MaterialTheme.colorScheme.error)
                            node.record.dirty -> MicroTag("待同步", SimingBlue)
                        }
                    }
                }
                if (!ordering && node.nodeType != "section") {
                    IconButton(onClick = onAddChild) {
                        Icon(Icons.Outlined.Add, "在${node.title}下新增")
                    }
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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun OutlineDetailScreen(
    projectId: String,
    target: OutlineEditorTarget,
    records: List<ReplicaEntity>,
    viewModel: MainViewModel,
    onBack: () -> Unit,
    onAddChild: (ReplicaEntity) -> Unit,
) {
    val record = target.record
    val creating = record == null
    val suggestedParent = records.firstOrNull { it.entityId == target.suggestedParentId }
    val initialType = record?.formText("node_type")?.ifBlank { "chapter" }
        ?: outlineSuggestedChildType(suggestedParent?.formText("node_type"))
    var editing by rememberSaveable(record?.key, target.suggestedParentId) { mutableStateOf(creating) }
    var title by rememberSaveable(record?.key, target.suggestedParentId) { mutableStateOf(record?.formText("title").orEmpty()) }
    var nodeType by rememberSaveable(record?.key, target.suggestedParentId) { mutableStateOf(initialType) }
    var parentId by rememberSaveable(record?.key, target.suggestedParentId) {
        mutableStateOf(record?.formText("parent_id")?.trim()?.takeIf(String::isNotEmpty) ?: target.suggestedParentId)
    }
    var summary by rememberSaveable(record?.key, target.suggestedParentId) { mutableStateOf(record?.formText("summary").orEmpty()) }
    var status by rememberSaveable(record?.key, target.suggestedParentId) { mutableStateOf(record?.formText("status").orEmpty().ifBlank { "pending" }) }
    var charactersJson by rememberSaveable(record?.key) { mutableStateOf(record?.formText("characters").orEmpty().ifBlank { "[]" }) }
    var metadataJson by rememberSaveable(record?.key) { mutableStateOf(record?.formText("metadata").orEmpty().ifBlank { "{}" }) }
    var showAdvanced by rememberSaveable(record?.key) { mutableStateOf(false) }
    var showParentPicker by remember { mutableStateOf(false) }
    var showMore by remember { mutableStateOf(false) }
    var showDelete by remember { mutableStateOf(false) }

    val parentRecord = records.firstOrNull { it.entityId == parentId }
    val parentType = parentRecord?.formText("node_type")?.ifBlank { "chapter" }
    val parentValid = validOutlineParentType(nodeType, parentType)
    val parentOptions = outlineParentOptions(records, record?.entityId, nodeType)
    val childCount = record?.let { current -> records.count { it.formText("parent_id") == current.entityId } } ?: 0

    fun reset() {
        if (creating) {
            onBack()
            return
        }
        title = record?.formText("title").orEmpty()
        nodeType = record?.formText("node_type").orEmpty().ifBlank { "chapter" }
        parentId = record?.formText("parent_id")?.trim()?.takeIf(String::isNotEmpty)
        summary = record?.formText("summary").orEmpty()
        status = record?.formText("status").orEmpty().ifBlank { "pending" }
        charactersJson = record?.formText("characters").orEmpty().ifBlank { "[]" }
        metadataJson = record?.formText("metadata").orEmpty().ifBlank { "{}" }
        showAdvanced = false
        editing = false
    }

    Scaffold(
        containerColor = SimingPaper,
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Text(
                        when {
                            creating -> "新建大纲节点"
                            editing -> "编辑${outlineTypeLabel(nodeType)}"
                            else -> title.ifBlank { "未命名大纲" }
                        },
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = if (editing) ::reset else onBack) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回")
                    }
                },
                actions = {
                    if (!creating && !editing) {
                        IconButton(onClick = { showMore = true }) { Icon(Icons.Outlined.MoreHoriz, "更多") }
                    }
                },
            )
        },
        bottomBar = {
            Surface(color = SimingPaperWarm, tonalElevation = 2.dp) {
                if (editing) {
                    Button(
                        enabled = title.isNotBlank() && parentValid,
                        onClick = {
                            val fields = linkedMapOf<String, Any?>(
                                "title" to title.trim(),
                                "node_type" to nodeType,
                                "parent_id" to parentId,
                                "summary" to summary,
                                "status" to status,
                                "sort_order" to outlineSortOrderForSave(record, records, parentId),
                            )
                            if (creating || showAdvanced) {
                                val parsedCharacters = runCatching { outlineEditorJson.parseToJsonElement(charactersJson) as? JsonArray }.getOrNull()
                                val parsedMetadata = runCatching { outlineEditorJson.parseToJsonElement(metadataJson) as? JsonObject }.getOrNull()
                                if (parsedCharacters == null) {
                                    viewModel.reportError("角色与场景职责必须是 JSON 数组")
                                    return@Button
                                }
                                if (parsedMetadata == null) {
                                    viewModel.reportError("高级元数据必须是 JSON 对象")
                                    return@Button
                                }
                                fields["characters"] = parsedCharacters
                                fields["metadata"] = parsedMetadata
                            }
                            viewModel.saveRecord(
                                projectId,
                                "outline",
                                record?.entityId,
                                fields,
                                record?.payload(),
                                onBack,
                            )
                        },
                        modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(14.dp),
                    ) {
                        Text("保存大纲")
                    }
                } else {
                    Row(
                        modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(12.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        if (record != null && nodeType != "section") {
                            OutlinedButton(onClick = { onAddChild(record) }, modifier = Modifier.weight(1f)) {
                                Icon(Icons.Outlined.Add, null)
                                Spacer(Modifier.width(6.dp))
                                Text(if (nodeType == "volume") "新增章" else "新增节")
                            }
                        }
                        Button(onClick = { editing = true }, modifier = Modifier.weight(1f)) {
                            Icon(Icons.Outlined.Edit, null)
                            Spacer(Modifier.width(6.dp))
                            Text("编辑")
                        }
                    }
                }
            }
        },
    ) { padding ->
        if (editing) {
            Column(
                modifier = Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Text("层级", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Row(
                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    listOf("volume" to "卷", "chapter" to "章", "section" to "节").forEach { (value, label) ->
                        AssistChip(
                            onClick = {
                                nodeType = value
                                val candidateType = records.firstOrNull { it.entityId == parentId }?.formText("node_type")
                                if (!validOutlineParentType(value, candidateType)) parentId = null
                            },
                            label = { Text(label) },
                        )
                    }
                }
                OutlinedTextField(title, { title = it.take(240) }, label = { Text("标题") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedButton(onClick = { showParentPicker = true }, modifier = Modifier.fillMaxWidth()) {
                    Text(
                        when {
                            nodeType == "volume" -> "父级：根级（卷）"
                            parentRecord != null -> "父级：${parentRecord.formText("title").ifBlank { "未命名节点" }}"
                            nodeType == "chapter" -> "父级：根级章节"
                            else -> "选择所属章"
                        },
                    )
                }
                if (!parentValid) {
                    Text("当前层级需要选择正确的父节点。节必须属于章；章可以属于卷或位于根级。", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                }
                OutlinedTextField(summary, { summary = it }, label = { Text("计划内容") }, minLines = 5, modifier = Modifier.fillMaxWidth())
                Text("进度", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Row(
                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    listOf("pending" to "待写", "in_progress" to "进行中", "completed" to "已完成").forEach { (value, label) ->
                        AssistChip(onClick = { status = value }, label = { Text(if (status == value) "✓ $label" else label) })
                    }
                }
                TextButton(onClick = { showAdvanced = !showAdvanced }) {
                    Text(if (showAdvanced) "收起高级结构数据" else "高级：场景角色与元数据")
                }
                if (showAdvanced) {
                    OutlinedTextField(charactersJson, { charactersJson = it }, label = { Text("角色与场景职责（JSON 数组）") }, minLines = 4, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(metadataJson, { metadataJson = it }, label = { Text("高级元数据（JSON 对象）") }, minLines = 4, modifier = Modifier.fillMaxWidth())
                    Text("这些字段与 PC 大纲契约保持一致，但默认隐藏，避免日常编辑被 JSON 干扰。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Spacer(Modifier.width(1.dp))
            }
        } else {
            LazyColumn(
                modifier = Modifier.padding(padding).fillMaxSize(),
                contentPadding = PaddingValues(18.dp, 18.dp, 18.dp, 96.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                item {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            MicroTag(outlineTypeLabel(nodeType), SimingCinnabar)
                            MicroTag(outlineStatusLabel(status), SimingBlue)
                            when {
                                record?.conflicted == true -> MicroTag("有分岔", MaterialTheme.colorScheme.error)
                                record?.dirty == true -> MicroTag("待同步", SimingBlue)
                            }
                        }
                        Text(title.ifBlank { "未命名大纲" }, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
                        Text(
                            when {
                                parentRecord != null -> "位于：${parentRecord.formText("title").ifBlank { "未命名父节点" }}"
                                nodeType == "volume" -> "根级卷"
                                else -> "根级结构"
                            },
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                item {
                    Surface(color = Color.White, shape = RoundedCornerShape(14.dp)) {
                        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("计划内容", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                            Text(summary.ifBlank { "还没有填写这一段剧情计划。" }, color = if (summary.isBlank()) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
                        }
                    }
                }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        Surface(color = SimingPaperWarm, shape = RoundedCornerShape(12.dp), modifier = Modifier.weight(1f)) {
                            Column(Modifier.padding(14.dp)) {
                                Text("子节点", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Text(childCount.toString(), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                            }
                        }
                        Surface(color = SimingPaperWarm, shape = RoundedCornerShape(12.dp), modifier = Modifier.weight(1f)) {
                            Column(Modifier.padding(14.dp)) {
                                Text("状态", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Text(outlineStatusLabel(status), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                }
            }
        }
    }

    if (showParentPicker) {
        AlertDialog(
            onDismissRequest = { showParentPicker = false },
            title = { Text("选择父级") },
            text = {
                Column(Modifier.heightIn(max = 440.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (nodeType != "section") {
                        TextButton(onClick = { parentId = null; showParentPicker = false }, modifier = Modifier.fillMaxWidth()) {
                            Text(if (nodeType == "volume") "根级（卷必须位于根级）" else "根级章节")
                        }
                    }
                    parentOptions.forEach { option ->
                        TextButton(onClick = { parentId = option.entityId; showParentPicker = false }, modifier = Modifier.fillMaxWidth()) {
                            Text("${outlineTypeLabel(option.formText("node_type"))} · ${option.formText("title").ifBlank { "未命名节点" }}")
                        }
                    }
                    if (nodeType == "section" && parentOptions.isEmpty()) {
                        Text("当前还没有可作为父级的章，请先创建章节级大纲。", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            },
            confirmButton = { TextButton(onClick = { showParentPicker = false }) { Text("关闭") } },
        )
    }

    if (showMore && record != null) {
        AlertDialog(
            onDismissRequest = { showMore = false },
            title = { Text("大纲操作") },
            text = { Text("删除会进入可靠同步队列；如果其他设备有离线修改，仍会按现有冲突保护处理。") },
            confirmButton = {
                TextButton(onClick = { showMore = false; showDelete = true }) {
                    Icon(Icons.Outlined.DeleteOutline, null)
                    Spacer(Modifier.width(6.dp))
                    Text("删除这个节点", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { showMore = false }) { Text("取消") } },
        )
    }

    if (showDelete && record != null) {
        AlertDialog(
            onDismissRequest = { showDelete = false },
            title = { Text("删除《${title.ifBlank { "未命名节点" }}》？") },
            text = {
                Text(
                    if (childCount > 0) {
                        "这个节点还有 $childCount 个直接子节点。PC 数据库会对父节点删除执行级联，因此手机端会阻止直接删除；请先移动或删除子节点。"
                    } else {
                        "删除后会同步到其他设备。"
                    },
                )
            },
            confirmButton = {
                if (childCount > 0) {
                    TextButton(onClick = { showDelete = false }) { Text("返回调整结构") }
                } else {
                    TextButton(onClick = { showDelete = false; viewModel.deleteRecord(projectId, "outline", record.entityId, onBack) }) {
                        Text("确认删除", color = MaterialTheme.colorScheme.error)
                    }
                }
            },
            dismissButton = {
                if (childCount == 0) TextButton(onClick = { showDelete = false }) { Text("取消") }
            },
        )
    }
}
