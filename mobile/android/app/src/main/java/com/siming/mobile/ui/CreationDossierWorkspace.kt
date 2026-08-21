package com.siming.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
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
import androidx.compose.ui.unit.sp
import com.siming.mobile.data.creation.CreationWorkbenchContract
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

/**
 * Structured Android creation workbench mirroring the desktop V3 wizard.
 *
 * The screen never invents a second mobile schema. It only displays and edits
 * the same V3 artifact objects consumed by the PC endpoints and by the
 * build-generated standalone creation contract.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun CreationDossierWorkspace(
    modifier: Modifier,
    session: JsonObject,
    stages: List<Pair<String, String>>,
    running: Boolean,
    activity: String,
    onBackToChat: () -> Unit,
    onGenerate: (stage: String, operation: String, instruction: String) -> Unit,
    onSave: (stage: String, data: JsonObject, onSaved: () -> Unit) -> Unit,
    onConfirm: (stage: String, data: JsonObject, onConfirmed: () -> Unit) -> Unit,
    onArchive: () -> Unit,
    onOpenProject: (String) -> Unit,
) {
    val stageOrder = remember(stages) { stages.map { it.first } }
    val labels = remember(stages) { stages.toMap() }
    var selectedStage by rememberSaveable(session.string("id")) {
        mutableStateOf(CreationWorkbenchContract.recommendedStage(session, stageOrder))
    }
    LaunchedEffect(session.int("revision"), stageOrder) {
        if (selectedStage !in stageOrder) {
            selectedStage = CreationWorkbenchContract.recommendedStage(session, stageOrder)
        }
    }

    val stageState = session.stageState(selectedStage)
    val stageStatus = stageState.string("status").ifBlank { "pending" }
    val stageData = stageState["data"] as? JsonObject ?: JsonObject(emptyMap())
    val stageLabel = labels[selectedStage] ?: selectedStage
    val canArchive = CreationWorkbenchContract.canArchive(session)
    val blockers = CreationWorkbenchContract.archiveBlockers(session, labels)
    val projectId = session.string("created_project_id")
    var selectedConceptId by rememberSaveable(session.string("id"), session.int("revision"), selectedStage) {
        mutableStateOf(CreationWorkbenchContract.selectedConceptId(session, stageData))
    }

    var editorOpen by rememberSaveable { mutableStateOf(false) }
    var editorText by rememberSaveable { mutableStateOf("") }
    var editorError by rememberSaveable { mutableStateOf<String?>(null) }
    var refineOpen by rememberSaveable { mutableStateOf(false) }
    var refineInstruction by rememberSaveable { mutableStateOf("") }
    var archiveConfirmOpen by rememberSaveable { mutableStateOf(false) }
    val prettyJson = remember { Json { prettyPrint = true } }

    fun currentDataForWrite(): JsonObject = if (selectedStage == "concepts") {
        CreationWorkbenchContract.conceptDataWithSelection(stageData, selectedConceptId)
    } else {
        stageData
    }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp, 10.dp, 16.dp, 112.dp),
        verticalArrangement = Arrangement.spacedBy(13.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBackToChat) {
                    Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回立项对话")
                }
                Column(Modifier.weight(1f)) {
                    Text("新书建档工作台", fontWeight = FontWeight.Bold, fontSize = 22.sp)
                    Text(
                        session.string("display_title")
                            .ifBlank { session.string("user_brief") }
                            .ifBlank { "未命名立项" },
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Text(
                        creationRouteLabel(session) + " · 草稿修订 ${session.int("revision")}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                StatusPill(stageStatus)
            }
        }

        item {
            Surface(
                color = Color(0xFFF3EEE8),
                shape = RoundedCornerShape(18.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.Lock, null, tint = SimingCinnabar)
                        Spacer(Modifier.width(8.dp))
                        Text("与 PC 使用同一份 V3 建档资料", fontWeight = FontWeight.Bold)
                    }
                    Text(
                        "这里的创意、世界观、角色、地点、卷纲、开篇细纲和最终审阅，字段结构与确认规则都和 PC 一致。手机独立模式只把执行位置换成本机，不会另造一套数据。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        item {
            Text("建档进度", fontWeight = FontWeight.Bold, fontSize = 17.sp)
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(7.dp),
            ) {
                stages.forEach { (stage, label) ->
                    val status = session.stageState(stage).string("status").ifBlank { "pending" }
                    AssistChip(
                        onClick = { selectedStage = stage },
                        label = { Text("${stageMarker(status)} $label") },
                        colors = AssistChipDefaults.assistChipColors(
                            containerColor = when {
                                stage == selectedStage -> MaterialTheme.colorScheme.primaryContainer
                                status == "confirmed" -> Color(0xFFEAF4EF)
                                status in setOf("stale", "conflict") -> Color(0xFFFFEEE9)
                                else -> Color.White
                            },
                        ),
                    )
                }
            }
        }

        if (stageStatus in setOf("stale", "conflict")) {
            item {
                Surface(
                    color = Color(0xFFFFEEE9),
                    shape = RoundedCornerShape(16.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(Modifier.padding(14.dp), verticalAlignment = Alignment.Top) {
                        Icon(Icons.Outlined.WarningAmber, null, tint = SimingCinnabar)
                        Spacer(Modifier.width(9.dp))
                        Column {
                            Text(if (stageStatus == "conflict") "该阶段存在版本冲突" else "上游修改后需要重新校验", fontWeight = FontWeight.Bold)
                            Text(
                                stageState.string("stale_reason").ifBlank { "请检查当前内容，必要时重新生成或编辑后再确认。" },
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }
            }
        }

        item {
            OutlinedCard(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(20.dp),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(stageLabel, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                            Text(
                                stageStatusDescription(stageStatus),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (stageData.isNotEmpty()) {
                            IconButton(
                                onClick = {
                                    editorText = prettyJson.encodeToString(JsonObject.serializer(), currentDataForWrite())
                                    editorError = null
                                    editorOpen = true
                                },
                                enabled = !running,
                            ) {
                                Icon(Icons.Outlined.Edit, "完整编辑")
                            }
                        }
                    }
                    HorizontalDivider()
                    when {
                        stageData.isEmpty() -> EmptyArtifact(stageLabel)
                        selectedStage == "concepts" -> ConceptSelector(
                            data = stageData,
                            selectedId = selectedConceptId,
                            onSelect = { selectedConceptId = it },
                            enabled = !running,
                        )
                        else -> ArtifactPreview(stageData)
                    }
                }
            }
        }

        item {
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(9.dp),
                verticalArrangement = Arrangement.spacedBy(9.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Button(
                    onClick = {
                        onGenerate(
                            selectedStage,
                            if (stageData.isEmpty()) "generate" else "regenerate",
                            "",
                        )
                    },
                    enabled = !running && CreationWorkbenchContract.stageCanGenerate(session, selectedStage),
                ) {
                    Icon(if (stageData.isEmpty()) Icons.Outlined.AutoAwesome else Icons.Outlined.Refresh, null)
                    Spacer(Modifier.width(7.dp))
                    Text(if (stageData.isEmpty()) "生成$stageLabel" else "重新生成")
                }
                OutlinedButton(
                    onClick = {
                        refineInstruction = ""
                        refineOpen = true
                    },
                    enabled = !running && stageData.isNotEmpty(),
                ) {
                    Icon(Icons.Outlined.Edit, null)
                    Spacer(Modifier.width(7.dp))
                    Text("按要求调整")
                }
                OutlinedButton(
                    onClick = {
                        editorText = prettyJson.encodeToString(JsonObject.serializer(), currentDataForWrite())
                        editorError = null
                        editorOpen = true
                    },
                    enabled = !running && stageData.isNotEmpty(),
                ) {
                    Text("完整编辑")
                }
            }
        }

        if (stageData.isNotEmpty()) {
            item {
                Button(
                    onClick = {
                        val data = currentDataForWrite()
                        val next = CreationWorkbenchContract.nextStage(stageOrder, selectedStage)
                        onConfirm(selectedStage, data) {
                            if (next != null) selectedStage = next
                        }
                    },
                    enabled = !running && CreationWorkbenchContract.stageCanConfirm(
                        if (selectedStage == "concepts" && selectedConceptId.isNotBlank()) {
                            session.withStageData("concepts", currentDataForWrite())
                        } else {
                            session
                        },
                        selectedStage,
                    ),
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(15.dp),
                ) {
                    Icon(Icons.Outlined.CheckCircle, null)
                    Spacer(Modifier.width(8.dp))
                    Text(
                        if (CreationWorkbenchContract.nextStage(stageOrder, selectedStage) == null) {
                            "确认$stageLabel"
                        } else {
                            "确认并进入下一项"
                        },
                        fontWeight = FontWeight.Bold,
                    )
                }
                if (selectedStage == "concepts" && selectedConceptId.isBlank()) {
                    Text(
                        "请先选择一个创意方向，再确认进入后续建档。",
                        style = MaterialTheme.typography.bodySmall,
                        color = SimingCinnabar,
                    )
                }
            }
        }

        if (running) {
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = Color(0xFF272725)),
                    shape = RoundedCornerShape(18.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(Modifier.padding(15.dp), verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp, color = Color(0xFFFFC6B3))
                        Spacer(Modifier.width(11.dp))
                        Column {
                            Text("建档任务正在执行", color = Color.White, fontWeight = FontWeight.Bold)
                            Text(
                                activity.ifBlank { "正在读取同一份立项资料并写入新修订…" },
                                color = Color.White.copy(alpha = 0.74f),
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }
            }
        }

        item {
            OutlinedCard(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(20.dp),
                border = BorderStroke(
                    1.5.dp,
                    if (canArchive) SimingGreen else MaterialTheme.colorScheme.outlineVariant,
                ),
                colors = CardDefaults.outlinedCardColors(
                    containerColor = if (canArchive) Color(0xFFF1F8F4) else Color.White,
                ),
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("创建正式作品", fontWeight = FontWeight.Bold, fontSize = 18.sp)
                    if (projectId.isNotBlank()) {
                        Text("该立项已经完成正式建档。", color = SimingGreen)
                        Button(
                            onClick = { onOpenProject(projectId) },
                            enabled = !running,
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Icon(Icons.Outlined.FolderOpen, null)
                            Spacer(Modifier.width(8.dp))
                            Text("打开正式作品")
                        }
                    } else if (canArchive) {
                        Text(
                            "最终审阅已通过。手机独立模式会把同一 V3 草稿投影为作品、角色、关系、世界观和大纲，并写入可同步的本地修订队列。",
                            style = MaterialTheme.typography.bodySmall,
                        )
                        Button(
                            onClick = { archiveConfirmOpen = true },
                            enabled = !running,
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Icon(Icons.Outlined.FolderOpen, null)
                            Spacer(Modifier.width(8.dp))
                            Text("建立正式作品", fontWeight = FontWeight.Bold)
                        }
                    } else {
                        Text("还差以下内容：", style = MaterialTheme.typography.bodySmall)
                        blockers.take(6).forEach { blocker ->
                            Text("• $blocker", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }

        item {
            Text(
                "手机独立建档与 PC 使用相同的阶段结构、字段校验、确认门槛和正式作品公共契约；连接 Gateway 后，在线操作直接交给 PC 权威建档服务执行。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }

    if (editorOpen) {
        AlertDialog(
            onDismissRequest = { if (!running) editorOpen = false },
            title = { Text("完整编辑 · $stageLabel") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "字段名与 PC 完整编辑器一致。保存后，下游受影响阶段会按同一依赖规则重新校验。",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    OutlinedTextField(
                        value = editorText,
                        onValueChange = {
                            editorText = it
                            editorError = null
                        },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 260.dp, max = 520.dp),
                        minLines = 12,
                        label = { Text("JSON 结构") },
                        isError = editorError != null,
                        supportingText = { editorError?.let { Text(it) } },
                    )
                }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val parsed = runCatching { Json.parseToJsonElement(editorText) as? JsonObject }
                            .getOrNull()
                        if (parsed == null) {
                            editorError = "内容不是有效的 JSON 对象，请检查括号、逗号和引号。"
                        } else {
                            onSave(selectedStage, parsed) {
                                editorOpen = false
                                editorError = null
                            }
                        }
                    },
                    enabled = !running,
                ) { Text("保存修改") }
            },
            dismissButton = {
                TextButton(onClick = { editorOpen = false }, enabled = !running) { Text("取消") }
            },
        )
    }

    if (refineOpen) {
        AlertDialog(
            onDismissRequest = { if (!running) refineOpen = false },
            title = { Text("让 AI 调整$stageLabel") },
            text = {
                OutlinedTextField(
                    value = refineInstruction,
                    onValueChange = { refineInstruction = it.take(2_000) },
                    label = { Text("本次调整要求") },
                    placeholder = { Text("例如：保留主角姓名，只把核心冲突改得更具持续性") },
                    minLines = 5,
                    maxLines = 10,
                    modifier = Modifier.fillMaxWidth(),
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val instruction = refineInstruction.trim()
                        if (instruction.isNotBlank()) {
                            refineOpen = false
                            onGenerate(selectedStage, "refine", instruction)
                        }
                    },
                    enabled = !running && refineInstruction.isNotBlank(),
                ) { Text("开始调整") }
            },
            dismissButton = {
                TextButton(onClick = { refineOpen = false }, enabled = !running) { Text("取消") }
            },
        )
    }

    if (archiveConfirmOpen) {
        AlertDialog(
            onDismissRequest = { if (!running) archiveConfirmOpen = false },
            title = { Text("确认建立正式作品？") },
            text = {
                Text("建档会创建作品、角色、关系、世界观与大纲。立项草稿会保留为已完成状态，之后仍可查看真实来源。")
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        archiveConfirmOpen = false
                        onArchive()
                    },
                    enabled = !running,
                ) { Text("确认建档") }
            },
            dismissButton = {
                TextButton(onClick = { archiveConfirmOpen = false }, enabled = !running) { Text("再检查一下") }
            },
        )
    }
}

@Composable
private fun StatusPill(status: String) {
    val (label, background, foreground) = when (status) {
        "confirmed" -> Triple("已确认", Color(0xFFE7F3EC), SimingGreen)
        "generated" -> Triple("待确认", Color(0xFFEAF1F7), SimingBlue)
        "stale" -> Triple("需校验", Color(0xFFFFEEE9), SimingCinnabar)
        "conflict" -> Triple("有冲突", Color(0xFFFFE5E5), Color(0xFF9B1C1C))
        else -> Triple("待生成", MaterialTheme.colorScheme.surfaceVariant, MaterialTheme.colorScheme.onSurfaceVariant)
    }
    Surface(color = background, shape = RoundedCornerShape(12.dp)) {
        Text(label, color = foreground, fontSize = 11.sp, modifier = Modifier.padding(horizontal = 9.dp, vertical = 5.dp))
    }
}

private fun stageMarker(status: String): String = when (status) {
    "confirmed" -> "✓"
    "generated" -> "•"
    "stale", "conflict" -> "!"
    else -> "○"
}

private fun stageStatusDescription(status: String): String = when (status) {
    "confirmed" -> "作者已确认；仍可编辑或重新生成，受影响的下游会重新校验。"
    "generated" -> "内容已保存，等待作者检查与确认。"
    "stale" -> "上游资料已经变化，需要重新检查后确认。"
    "conflict" -> "当前内容与最新修订冲突，需要编辑或重新生成。"
    else -> "尚未生成；AI 生成后不会自动确认。"
}

private fun creationRouteLabel(session: JsonObject): String {
    val draft = session.objectValue("draft")
    return when {
        draft.string("execution_route") == "pc" -> "电脑线路 · PC 权威建档服务"
        draft.string("execution_host") == "gateway" -> "手机 Key · PC 权威建档服务"
        else -> "手机独立 · PC 同源建档引擎"
    }
}

private fun JsonObject.withStageData(stage: String, data: JsonObject): JsonObject {
    val root = toMutableMap()
    val draft = objectValue("draft").toMutableMap()
    val stages = objectValue("draft").objectValue("stages").toMutableMap()
    val state = (stages[stage] as? JsonObject ?: JsonObject(emptyMap())).toMutableMap()
    state["data"] = data
    stages[stage] = JsonObject(state)
    draft["stages"] = JsonObject(stages)
    root["draft"] = JsonObject(draft)
    return JsonObject(root)
}

private fun JsonObject.stageState(stage: String): JsonObject =
    objectValue("draft").objectValue("stages").objectValue(stage)
private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
private fun JsonObject.int(name: String): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: 0
