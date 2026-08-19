package com.siming.mobile.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Flag
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
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

internal fun narrativeStatusLabel(raw: String): String = when (raw) {
    "pending_review" -> "待复检"
    "deferred" -> "已延期"
    "fulfilled" -> "已兑现"
    "abandoned" -> "已放弃"
    else -> "进行中"
}

internal fun narrativePriorityLabel(raw: String): String = when (raw) {
    "critical" -> "关键"
    "high" -> "高"
    "low" -> "低"
    else -> "中"
}

internal fun narrativeDebtTypeLabel(raw: String): String = when (raw) {
    "setup" -> "铺垫"
    "obligation" -> "义务"
    "question" -> "悬念"
    else -> "承诺"
}

private fun narrativePriorityRank(raw: String): Int = when (raw) {
    "critical" -> 0
    "high" -> 1
    "medium" -> 2
    "low" -> 3
    else -> 4
}

private fun narrativeStatusRank(raw: String): Int = when (raw) {
    "open" -> 0
    "pending_review" -> 1
    "deferred" -> 2
    "fulfilled" -> 3
    "abandoned" -> 4
    else -> 5
}

@Composable
internal fun NarrativeWorkspace(
    entityType: String,
    records: List<ReplicaEntity>,
    onOpen: (ReplicaEntity) -> Unit,
) {
    val isForeshadowing = entityType == "foreshadowing"
    val priorityKey = if (isForeshadowing) "importance" else "priority"
    val sorted = remember(records, entityType) {
        records.sortedWith(
            compareBy<ReplicaEntity>(
                { narrativeStatusRank(it.formText("status")) },
                { narrativePriorityRank(it.formText(priorityKey)) },
                { it.formText("title") },
            ),
        )
    }
    val active = records.count { it.formText("status") !in setOf("fulfilled", "abandoned") }
    val completed = records.size - active

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 104.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                ScreenHeading(
                    kicker = "",
                    title = if (isForeshadowing) "伏笔追踪" else "叙事承诺",
                    detail = if (isForeshadowing) {
                        "记录埋设、计划回收和复检结果；技术 ID 默认收进高级详情。"
                    } else {
                        "把读者期待、必须兑现的承诺和悬念当作可追踪任务管理。"
                    },
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    MicroTag("$active 进行中", SimingCinnabar)
                    MicroTag("$completed 已收口", SimingGreen)
                }
            }
        }
        if (records.isEmpty()) {
            item {
                EmptyPanel(
                    if (isForeshadowing) Icons.Outlined.Flag else Icons.Outlined.TaskAlt,
                    if (isForeshadowing) "还没有伏笔" else "还没有叙事承诺",
                    if (isForeshadowing) "点击右下角“＋”记录一个需要回收的伏笔。" else "点击右下角“＋”记录一个需要兑现的读者期待。",
                )
            }
        } else {
            items(sorted, key = { it.key }) { record ->
                NarrativeCard(entityType, record, onOpen)
            }
        }
    }
}

@Composable
private fun NarrativeCard(entityType: String, record: ReplicaEntity, onOpen: (ReplicaEntity) -> Unit) {
    val isForeshadowing = entityType == "foreshadowing"
    val priorityKey = if (isForeshadowing) "importance" else "priority"
    val status = record.formText("status").ifBlank { "open" }
    val priority = record.formText(priorityKey).ifBlank { "medium" }
    val description = record.formText("description")
    val target = record.formText("target_chapter_number")
    val typeLabel = if (isForeshadowing) record.formText("storyline") else narrativeDebtTypeLabel(record.formText("debt_type"))

    OutlinedCard(onClick = { onOpen(record) }, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        record.formText("title").ifBlank { if (isForeshadowing) "未命名伏笔" else "未命名承诺" },
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (description.isNotBlank()) {
                        Text(
                            description,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
                when {
                    record.conflicted -> MicroTag("有分岔", MaterialTheme.colorScheme.error)
                    record.dirty -> MicroTag("待同步", SimingBlue)
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(7.dp), verticalAlignment = Alignment.CenterVertically) {
                MicroTag(narrativeStatusLabel(status), if (status == "fulfilled") SimingGreen else SimingCinnabar)
                MicroTag("${narrativePriorityLabel(priority)}优先级", SimingBlue)
                if (typeLabel.isNotBlank()) {
                    Text(typeLabel, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (target.isNotBlank()) {
                    Text("目标第 $target 章", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun NarrativeDetailScreen(
    projectId: String,
    entityType: String,
    record: ReplicaEntity?,
    viewModel: MainViewModel,
    onBack: () -> Unit,
) {
    val creating = record == null
    val isForeshadowing = entityType == "foreshadowing"
    val priorityKey = if (isForeshadowing) "importance" else "priority"
    var editing by rememberSaveable(record?.key, entityType) { mutableStateOf(creating) }
    var title by rememberSaveable(record?.key) { mutableStateOf(record?.formText("title").orEmpty()) }
    var description by rememberSaveable(record?.key) { mutableStateOf(record?.formText("description").orEmpty()) }
    var status by rememberSaveable(record?.key) { mutableStateOf(record?.formText("status").orEmpty().ifBlank { "open" }) }
    var priority by rememberSaveable(record?.key) { mutableStateOf(record?.formText(priorityKey).orEmpty().ifBlank { "medium" }) }
    var storyline by rememberSaveable(record?.key) { mutableStateOf(record?.formText("storyline").orEmpty()) }
    var debtType by rememberSaveable(record?.key) { mutableStateOf(record?.formText("debt_type").orEmpty().ifBlank { "promise" }) }
    var targetNumber by rememberSaveable(record?.key) { mutableStateOf(record?.formText("target_chapter_number").orEmpty()) }
    var sourceChapterId by rememberSaveable(record?.key) { mutableStateOf(record?.formText("source_chapter_id").orEmpty()) }
    var targetChapterId by rememberSaveable(record?.key) { mutableStateOf(record?.formText("target_chapter_id").orEmpty()) }
    var resolvedChapterId by rememberSaveable(record?.key) { mutableStateOf(record?.formText("resolved_chapter_id").orEmpty()) }
    var linkedForeshadowingId by rememberSaveable(record?.key) { mutableStateOf(record?.formText("linked_foreshadowing_id").orEmpty()) }
    var linkedCausalEdgeId by rememberSaveable(record?.key) { mutableStateOf(record?.formText("linked_causal_edge_id").orEmpty()) }
    var evidence by rememberSaveable(record?.key) { mutableStateOf(record?.formText("evidence").orEmpty()) }
    var resolutionNote by rememberSaveable(record?.key) { mutableStateOf(record?.formText("resolution_note").orEmpty()) }
    var resolutionEvidence by rememberSaveable(record?.key) { mutableStateOf(record?.formText("resolution_evidence").orEmpty()) }
    var verificationNote by rememberSaveable(record?.key) { mutableStateOf(record?.formText("verification_note").orEmpty()) }
    var closedBy by rememberSaveable(record?.key) { mutableStateOf(record?.formText("closed_by").orEmpty()) }
    var showAdvanced by rememberSaveable(record?.key) { mutableStateOf(false) }
    var showMore by remember { mutableStateOf(false) }
    var showDelete by remember { mutableStateOf(false) }

    fun reset() {
        if (creating) {
            onBack()
            return
        }
        title = record?.formText("title").orEmpty()
        description = record?.formText("description").orEmpty()
        status = record?.formText("status").orEmpty().ifBlank { "open" }
        priority = record?.formText(priorityKey).orEmpty().ifBlank { "medium" }
        storyline = record?.formText("storyline").orEmpty()
        debtType = record?.formText("debt_type").orEmpty().ifBlank { "promise" }
        targetNumber = record?.formText("target_chapter_number").orEmpty()
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
                            creating -> if (isForeshadowing) "新伏笔" else "新叙事承诺"
                            editing -> "编辑"
                            else -> title.ifBlank { if (isForeshadowing) "未命名伏笔" else "未命名承诺" }
                        },
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = { IconButton(onClick = if (editing) ::reset else onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回") } },
                actions = {
                    if (!creating && !editing) IconButton(onClick = { showMore = true }) { Icon(Icons.Outlined.MoreHoriz, "更多") }
                },
            )
        },
        bottomBar = {
            Surface(color = SimingPaperWarm, tonalElevation = 2.dp) {
                if (editing) {
                    Button(
                        enabled = title.isNotBlank(),
                        onClick = {
                            val fields = linkedMapOf<String, Any?>(
                                "title" to title.trim(),
                                "description" to description,
                                "status" to status,
                                priorityKey to priority,
                                "target_chapter_number" to targetNumber.trim().takeIf(String::isNotBlank)?.toIntOrNull(),
                            )
                            if (isForeshadowing) fields["storyline"] = storyline.trim().takeIf(String::isNotBlank)
                            else fields["debt_type"] = debtType
                            if (creating || showAdvanced) {
                                fields["source_chapter_id"] = sourceChapterId.trim().takeIf(String::isNotBlank)
                                fields["target_chapter_id"] = targetChapterId.trim().takeIf(String::isNotBlank)
                                fields["resolved_chapter_id"] = resolvedChapterId.trim().takeIf(String::isNotBlank)
                                fields["evidence"] = evidence
                                fields["resolution_note"] = resolutionNote
                                fields["resolution_evidence"] = resolutionEvidence
                                fields["verification_note"] = verificationNote
                                fields["closed_by"] = closedBy.trim().takeIf(String::isNotBlank)
                                if (!isForeshadowing) {
                                    fields["linked_foreshadowing_id"] = linkedForeshadowingId.trim().takeIf(String::isNotBlank)
                                    fields["linked_causal_edge_id"] = linkedCausalEdgeId.trim().takeIf(String::isNotBlank)
                                }
                            }
                            viewModel.saveRecord(projectId, entityType, record?.entityId, fields, record?.payload(), onBack)
                        },
                        modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(14.dp),
                    ) {
                        Text("保存")
                    }
                } else {
                    Button(
                        onClick = { editing = true },
                        modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(14.dp),
                    ) {
                        Icon(Icons.Outlined.Edit, null)
                        Spacer(Modifier.width(7.dp))
                        Text("编辑")
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
                OutlinedTextField(title, { title = it.take(240) }, label = { Text("标题") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(description, { description = it }, label = { Text(if (isForeshadowing) "埋设与回收计划" else "读者期待与兑现条件") }, minLines = 5, modifier = Modifier.fillMaxWidth())
                Text("状态", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Row(modifier = Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(
                        "open" to "进行中",
                        "pending_review" to "待复检",
                        "deferred" to "延期",
                        "fulfilled" to "已兑现",
                        "abandoned" to "放弃",
                    ).forEach { (value, label) ->
                        AssistChip(onClick = { status = value }, label = { Text(if (status == value) "✓ $label" else label) })
                    }
                }
                Text(if (isForeshadowing) "重要度" else "优先级", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Row(modifier = Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("low" to "低", "medium" to "中", "high" to "高", "critical" to "关键").forEach { (value, label) ->
                        AssistChip(onClick = { priority = value }, label = { Text(if (priority == value) "✓ $label" else label) })
                    }
                }
                if (isForeshadowing) {
                    OutlinedTextField(storyline, { storyline = it }, label = { Text("故事线（可选）") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                } else {
                    Text("承诺类型", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Row(modifier = Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf("promise" to "承诺", "setup" to "铺垫", "obligation" to "义务", "question" to "悬念").forEach { (value, label) ->
                            AssistChip(onClick = { debtType = value }, label = { Text(if (debtType == value) "✓ $label" else label) })
                        }
                    }
                }
                OutlinedTextField(targetNumber, { targetNumber = it.filter(Char::isDigit).take(8) }, label = { Text("计划处理章节号（可选）") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                TextButton(onClick = { showAdvanced = !showAdvanced }) {
                    Text(if (showAdvanced) "收起追踪详情" else "高级：证据、章节 ID 与复检信息")
                }
                if (showAdvanced) {
                    OutlinedTextField(sourceChapterId, { sourceChapterId = it }, label = { Text("来源章节 ID") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(targetChapterId, { targetChapterId = it }, label = { Text("计划处理章节 ID") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(resolvedChapterId, { resolvedChapterId = it }, label = { Text("实际解决章节 ID") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    if (!isForeshadowing) {
                        OutlinedTextField(linkedForeshadowingId, { linkedForeshadowingId = it }, label = { Text("关联伏笔 ID") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(linkedCausalEdgeId, { linkedCausalEdgeId = it }, label = { Text("关联因果项 ID") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    }
                    OutlinedTextField(evidence, { evidence = it }, label = { Text("发现证据") }, minLines = 3, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(resolutionNote, { resolutionNote = it }, label = { Text("解决说明") }, minLines = 3, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(resolutionEvidence, { resolutionEvidence = it }, label = { Text("解决证据") }, minLines = 3, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(verificationNote, { verificationNote = it }, label = { Text("复检结论") }, minLines = 3, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(closedBy, { closedBy = it }, label = { Text("关闭者") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    Text("技术关联字段默认隐藏；普通编辑通过 base payload 保留，不会因为手机界面简化而丢失。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
                            MicroTag(narrativeStatusLabel(status), if (status == "fulfilled") SimingGreen else SimingCinnabar)
                            MicroTag("${narrativePriorityLabel(priority)}优先级", SimingBlue)
                            if (!isForeshadowing) MicroTag(narrativeDebtTypeLabel(debtType), SimingGreen)
                        }
                        Text(title.ifBlank { if (isForeshadowing) "未命名伏笔" else "未命名承诺" }, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
                        when {
                            record?.conflicted == true -> MicroTag("有版本分岔", MaterialTheme.colorScheme.error)
                            record?.dirty == true -> MicroTag("等待同步", SimingBlue)
                        }
                    }
                }
                item {
                    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(14.dp)) {
                        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(if (isForeshadowing) "埋设与回收计划" else "读者期待与兑现条件", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                            Text(description.ifBlank { "还没有填写具体计划。" }, color = if (description.isBlank()) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
                        }
                    }
                }
                if (targetNumber.isNotBlank() || storyline.isNotBlank()) {
                    item {
                        OutlinedCard(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text("计划", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                                if (targetNumber.isNotBlank()) Text("计划在第 $targetNumber 章处理")
                                if (storyline.isNotBlank()) Text("故事线：$storyline", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
                if (resolutionNote.isNotBlank() || verificationNote.isNotBlank()) {
                    item {
                        OutlinedCard(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                                Text("收口记录", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                                if (resolutionNote.isNotBlank()) Text(resolutionNote)
                                if (verificationNote.isNotBlank()) Text("复检：$verificationNote", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
            }
        }
    }

    if (showMore && record != null) {
        AlertDialog(
            onDismissRequest = { showMore = false },
            title = { Text("更多操作") },
            text = { Text("删除会沿用现有同步与版本分岔保护，不会静默覆盖其他设备的离线修改。") },
            confirmButton = {
                TextButton(onClick = { showMore = false; showDelete = true }) {
                    Icon(Icons.Outlined.DeleteOutline, null)
                    Spacer(Modifier.width(7.dp))
                    Text("删除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { showMore = false }) { Text("取消") } },
        )
    }

    if (showDelete && record != null) {
        AlertDialog(
            onDismissRequest = { showDelete = false },
            title = { Text("确认删除？") },
            text = { Text(title.ifBlank { if (isForeshadowing) "这条伏笔" else "这条叙事承诺" }) },
            confirmButton = {
                TextButton(onClick = { showDelete = false; viewModel.deleteRecord(projectId, entityType, record.entityId, onBack) }) {
                    Text("确认删除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { showDelete = false }) { Text("取消") } },
        )
    }
}
