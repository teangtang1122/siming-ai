package com.siming.mobile.ui

import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.foundation.layout.imePadding

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
import androidx.compose.foundation.lazy.rememberLazyListState
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
    initialStage: String? = null,
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
        mutableStateOf(initialStage?.takeIf { it in stageOrder }
            ?: CreationWorkbenchContract.recommendedStage(session, stageOrder))
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
    var refineOpen by rememberSaveable { mutableStateOf(false) }
    var refineInstruction by rememberSaveable { mutableStateOf("") }
    var archiveConfirmOpen by rememberSaveable { mutableStateOf(false) }
    var showStages by rememberSaveable { mutableStateOf(false) }

    fun currentDataForWrite(): JsonObject = if (selectedStage == "concepts") {
        CreationWorkbenchContract.conceptDataWithSelection(stageData, selectedConceptId)
    } else {
        stageData
    }

    val listState = rememberLazyListState()
    LaunchedEffect(selectedStage) { listState.scrollToItem(0) }
    Column(modifier.fillMaxSize().imePadding()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBackToChat) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回立项对话") }
            TextButton(onClick = { showStages = true }, modifier = Modifier.weight(1f)) {
                Text(stageLabel, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(" ▾")
            }
            StatusPill(stageStatus)
        }
        LinearProgressIndicator(progress = { stages.count { session.stageState(it.first).string("status") == "confirmed" }.toFloat() / stages.size.coerceAtLeast(1) },
            modifier = Modifier.fillMaxWidth())
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth(),
            contentPadding = PaddingValues(16.dp, 14.dp, 16.dp, 20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
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
                                    editorOpen = true
                                },
                                enabled = !running,
                            ) {
                                Icon(Icons.Outlined.Edit, "编辑资料")
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
                if (selectedStage != "constraints") {
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
                }
                OutlinedButton(
                    onClick = {
                        editorOpen = true
                    },
                    enabled = !running && stageData.isNotEmpty(),
                ) {
                    Text("编辑资料")
                }
            }
        }

        if (selectedStage == "constraints") {
            item {
                Text(
                    "修改约束后，受影响的后续资料需要重新确认。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
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

        if (canArchive || selectedStage == stageOrder.lastOrNull() || projectId.isNotBlank()) item {
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
                            "资料已齐备。创建作品后，就可以开始写正文。",
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

        }
        Surface(color = MaterialTheme.colorScheme.surface) {
            Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                if (projectId.isNotBlank()) {
                    Button(onClick = { onOpenProject(projectId) }, modifier = Modifier.fillMaxWidth()) { Text("打开正式作品") }
                } else if (canArchive && selectedStage == stageOrder.lastOrNull()) {
                    Button(onClick = { archiveConfirmOpen = true }, enabled = !running,
                        modifier = Modifier.fillMaxWidth()) { Text("建立正式作品") }
                } else if (stageStatus == "confirmed" && (selectedStage != "concepts" ||
                        selectedConceptId == CreationWorkbenchContract.selectedConceptId(session, stageData))) {
                    Button(onClick = {
                        CreationWorkbenchContract.nextStage(stageOrder, selectedStage)?.let { selectedStage = it }
                            ?: run { showStages = true }
                    }, enabled = !running, modifier = Modifier.fillMaxWidth()) { Text("已确认 · 查看下一项") }
                    if (selectedStage != "constraints") TextButton(
                        onClick = { onGenerate(selectedStage, "regenerate", "") },
                        enabled = !running && CreationWorkbenchContract.stageCanGenerate(session, selectedStage),
                        modifier = Modifier.fillMaxWidth()) { Text("重新生成") }
                } else if (stageData.isEmpty()) {
                    Button(onClick = { onGenerate(selectedStage, "generate", "") },
                        enabled = !running && selectedStage != "constraints" && CreationWorkbenchContract.stageCanGenerate(session, selectedStage),
                        modifier = Modifier.fillMaxWidth()) { Text(if (running) "生成中…" else "生成$stageLabel") }
                    if (!running && !CreationWorkbenchContract.stageCanGenerate(session, selectedStage))
                        Text("请先在资料目录确认前面的阶段。", style = MaterialTheme.typography.bodySmall)
                } else {
                    Button(onClick = {
                        val next = CreationWorkbenchContract.nextStage(stageOrder, selectedStage)
                        onConfirm(selectedStage, currentDataForWrite()) { if (next != null) selectedStage = next }
                    }, enabled = !running && CreationWorkbenchContract.stageCanConfirm(
                        if (selectedStage == "concepts" && selectedConceptId.isNotBlank()) session.withStageData("concepts", currentDataForWrite()) else session, selectedStage),
                        modifier = Modifier.fillMaxWidth()) {
                        Text(if (CreationWorkbenchContract.nextStage(stageOrder, selectedStage) == null) "确认审阅" else "确认并继续")
                    }
                    if (selectedStage != "constraints") TextButton(
                        onClick = { onGenerate(selectedStage, "regenerate", "") },
                        enabled = !running && CreationWorkbenchContract.stageCanGenerate(session, selectedStage),
                        modifier = Modifier.fillMaxWidth()) { Text("重新生成") }
                }
            }
        }
    }
    if (showStages) {
        CreationStageSheet(stages.map { CreationStageItem(it.first, it.second, session.stageState(it.first).string("status")) },
            selected = selectedStage, onSelected = { selectedStage = it; showStages = false }, onDismiss = { showStages = false })
    }

    if (editorOpen) {
        CreationArtifactEditor(title = stageLabel, initial = currentDataForWrite(), busy = running,
            onDismiss = { editorOpen = false }, onSave = { data ->
                onSave(selectedStage, data) { editorOpen = false }
            })
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

private fun stageStatusDescription(status: String): String = when (status) {
    "confirmed" -> "作者已确认；仍可编辑或重新生成，受影响的下游会重新校验。"
    "generated" -> "内容已保存，等待作者检查与确认。"
    "stale" -> "上游资料已经变化，需要重新检查后确认。"
    "conflict" -> "当前内容与最新修订冲突，需要编辑或重新生成。"
    else -> "尚未生成；AI 生成后不会自动确认。"
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
