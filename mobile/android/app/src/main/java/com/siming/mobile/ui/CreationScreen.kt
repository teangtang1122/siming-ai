package com.siming.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.imePadding
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.Surface

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.CloudQueue
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material3.Button
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
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
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.siming.mobile.data.creation.CreationExecutionRoute
import com.siming.mobile.data.creation.CreationStartInput
import com.siming.mobile.data.creation.CreationWorkbenchContract
import com.siming.mobile.data.creation.PcCreationPreset
import com.siming.mobile.data.creation.PcCreationPromptContract
import com.siming.mobile.data.local.GatewayConnection
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.network.DirectApiSummary
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

@Composable
internal fun CreationScreen(
    modifier: Modifier,
    viewModel: MainViewModel,
    connection: GatewayConnection?,
    directApi: DirectApiSummary?,
    onConfigureApi: () -> Unit,
    onOpenProject: (String) -> Unit,
) {
    val context = LocalContext.current
    val pcContract = remember(context) {
        PcCreationPromptContract(context.applicationContext)
    }
    val stages = remember(pcContract) {
        CreationWorkbenchContract.visibleStages(pcContract.stageOrder, pcContract.stageLabels)
    }
    val drafts by viewModel.creationDrafts.collectAsStateWithLifecycle()
    val ui by viewModel.uiState
    val active = drafts.firstOrNull { it.entityId == ui.activeCreationId }?.creationPayload()
    var showDossier by rememberSaveable(ui.activeCreationId) { mutableStateOf(false) }
    var dossierStage by rememberSaveable(ui.activeCreationId) { mutableStateOf<String?>(null) }


    BackHandler(enabled = active != null) {
        if (showDossier) showDossier = false else viewModel.closeCreation()
    }

    when {
        ui.activeCreationId != null && active == null -> Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        active != null && showDossier -> CreationDossierWorkspace(
            initialStage = dossierStage,
            modifier = modifier,
            session = active,
            stages = stages,
            running = ui.creationRunning,
            activity = ui.creationActivity,
            onBackToChat = { showDossier = false },
            onGenerate = { stage, operation, instruction ->
                viewModel.generateCreationStage(active.string("id"), stage, operation, instruction)
            },
            onSave = { stage, data, onSaved ->
                viewModel.saveCreationStage(active.string("id"), stage, data, onSaved)
            },
            onConfirm = { stage, data, onConfirmed ->
                viewModel.confirmCreationStage(active.string("id"), stage, data, onConfirmed)
            },
            onArchive = { viewModel.archiveCreation(active.string("id"), onOpenProject) },
            onOpenProject = onOpenProject,
        )
        active != null -> CreationConversationWorkspace(
            modifier = modifier,
            session = active,
            stages = stages,
            running = ui.creationRunning,
            activity = ui.creationActivity,
            replyDelta = ui.creationReplyDelta,
            progressEvents = ui.creationProgressEvents,
            onBack = viewModel::closeCreation,
            onOpenDossier = { stage -> dossierStage = stage; showDossier = true },
            onSend = { message -> viewModel.sendCreationMessage(active.string("id"), message) },
            onDiscard = { viewModel.discardCreation(active.string("id")) },
            onContinueOnPhone = { viewModel.continueCreationOnPhone(active.string("id")) },
            onConfigureApi = onConfigureApi,
            onOpenProject = onOpenProject,
        )
        else -> CreationLanding(
            modifier = modifier,
            drafts = drafts.mapNotNull(ReplicaEntity::creationPayload)
                .filter { it.string("status") != "completed" },
            connection = connection,
            directApi = directApi,
            presets = pcContract.presets,
            presetDefaults = pcContract::presetDefaults,
            stages = stages,
            running = ui.creationRunning,
            activity = ui.creationActivity,
            onConfigureApi = onConfigureApi,
            onResume = viewModel::resumeCreation,
            onStart = viewModel::beginCreation,
        )
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun CreationLanding(
    modifier: Modifier,
    drafts: List<JsonObject>,
    connection: GatewayConnection?,
    directApi: DirectApiSummary?,
    presets: List<PcCreationPreset>,
    presetDefaults: (String) -> JsonObject,
    stages: List<Pair<String, String>>,
    running: Boolean,
    activity: String,
    onConfigureApi: () -> Unit,
    onResume: (String) -> Unit,
    onStart: (CreationStartInput, CreationExecutionRoute) -> Unit,
) {
    var brief by rememberSaveable { mutableStateOf("") }
    var creationMode by rememberSaveable { mutableStateOf("author_led") }
    var route by rememberSaveable(connection?.deviceId, directApi?.model) {
        mutableStateOf(CreationExecutionRoute.MobileKey)
    }
    var advanced by rememberSaveable { mutableStateOf(false) }
    var authorOutline by rememberSaveable { mutableStateOf("") }
    var presetId by rememberSaveable { mutableStateOf("free") }
    var themeId by rememberSaveable { mutableStateOf("") }
    var genre by rememberSaveable { mutableStateOf("自由创作") }
    var audience by rememberSaveable { mutableStateOf("成年大众") }
    var platform by rememberSaveable { mutableStateOf("暂不确定") }
    var targetWords by rememberSaveable { mutableStateOf("600000") }
    var targetChapters by rememberSaveable { mutableStateOf("240") }
    var requirements by rememberSaveable { mutableStateOf("") }
    var avoid by rememberSaveable { mutableStateOf("") }
    var worldTone by rememberSaveable { mutableStateOf("") }
    var storyStructure by rememberSaveable { mutableStateOf("") }
    var pacing by rememberSaveable { mutableStateOf("") }
    var writingStyle by rememberSaveable { mutableStateOf("") }

    var showDrafts by rememberSaveable { mutableStateOf(false) }
    var showRoute by rememberSaveable { mutableStateOf(false) }
    Column(modifier.fillMaxSize().imePadding()) {
    LazyColumn(
        modifier = Modifier.weight(1f).fillMaxWidth(),
        contentPadding = PaddingValues(16.dp, 14.dp, 16.dp, 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("开启新故事", style = MaterialTheme.typography.headlineSmall)
                    Text("先说想法，再逐步完善。", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (drafts.isNotEmpty()) TextButton(onClick = { showDrafts = true }) { Text("立项记录") }
            }
        }
        item {
            OutlinedTextField(value = brief, onValueChange = { brief = it },
                label = { Text("你想写什么故事？") },
                placeholder = { Text("一个人物、一条设定，或一段已有构思…") },
                minLines = 4, maxLines = 8, modifier = Modifier.fillMaxWidth(), shape = MaterialTheme.shapes.medium)
        }
        item {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AssistChip(onClick = { creationMode = "author_led" }, label = { Text("按我的设定") },
                    colors = AssistChipDefaults.assistChipColors(containerColor = if (creationMode == "author_led") MaterialTheme.colorScheme.primaryContainer else Color.Transparent))
                AssistChip(onClick = { creationMode = "explore" }, label = { Text("帮我探索") },
                    colors = AssistChipDefaults.assistChipColors(containerColor = if (creationMode == "explore") MaterialTheme.colorScheme.primaryContainer else Color.Transparent))
            }
            Text(if (creationMode == "author_led") "保留你的设定，AI 整理并补充空白。" else "和 AI 一起寻找创意与故事方向。",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        item {
            TextButton(onClick = { advanced = !advanced }) {
                Text(if (advanced) "收起补充设定" else "补充题材、篇幅与要求（可选）")
            }
        }
        if (advanced) {
            item {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("题材模板", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                        AssistChip(
                            onClick = {
                                presetId = "free"
                                themeId = ""
                                genre = "自由创作"
                            },
                            label = { Text("自由创作") },
                            colors = AssistChipDefaults.assistChipColors(
                                containerColor = if (presetId == "free") MaterialTheme.colorScheme.primaryContainer else Color.White,
                            ),
                        )
                        presets.forEach { preset ->
                            AssistChip(
                                onClick = {
                                    presetId = preset.id
                                    themeId = ""
                                    genre = preset.label
                                    val defaults = presetDefaults(preset.id)
                                    worldTone = defaults.string("world_tone")
                                    storyStructure = defaults.string("story_structure")
                                    pacing = defaults.string("pacing")
                                    writingStyle = defaults.string("writing_style")
                                    requirements = defaults.linesText("special_requirements")
                                    avoid = defaults.linesText("avoid")
                                },
                                label = { Text(preset.label) },
                                colors = AssistChipDefaults.assistChipColors(
                                    containerColor = if (presetId == preset.id) MaterialTheme.colorScheme.primaryContainer else Color.White,
                                ),
                            )
                        }
                    }
                    presets.firstOrNull { it.id == presetId }?.let { preset ->
                        Text("细分方向（可选）", style = MaterialTheme.typography.labelMedium)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                            preset.themes.forEach { (value, theme) ->
                                AssistChip(
                                    onClick = { themeId = if (themeId == value) "" else value },
                                    label = { Text(theme) },
                                    colors = AssistChipDefaults.assistChipColors(
                                        containerColor = if (themeId == value) MaterialTheme.colorScheme.secondaryContainer else Color.White,
                                    ),
                                )
                            }
                        }
                    }
                    if (creationMode == "author_led") {
                        OutlinedTextField(
                            value = authorOutline,
                            onValueChange = { authorOutline = it },
                            label = { Text("已有大纲（可选）") },
                            minLines = 3,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        OutlinedTextField(genre, { genre = it }, label = { Text("题材") }, modifier = Modifier.weight(1f))
                        OutlinedTextField(audience, { audience = it }, label = { Text("目标读者") }, modifier = Modifier.weight(1f))
                    }
                    Text("篇幅预设", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                        listOf(
                            Triple("短篇", "30000", "15"),
                            Triple("中篇", "150000", "60"),
                            Triple("长篇", "600000", "240"),
                            Triple("超长连载", "2500000", "1000"),
                        ).forEach { (label, words, chapters) ->
                            AssistChip(
                                onClick = {
                                    targetWords = words
                                    targetChapters = chapters
                                },
                                label = { Text(label) },
                                colors = AssistChipDefaults.assistChipColors(
                                    containerColor = if (targetWords == words && targetChapters == chapters) MaterialTheme.colorScheme.secondaryContainer else Color.White,
                                ),
                            )
                        }
                    }
                    OutlinedTextField(platform, { platform = it }, label = { Text("发布平台") }, modifier = Modifier.fillMaxWidth())
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        OutlinedTextField(targetWords, { targetWords = it.filter(Char::isDigit) }, label = { Text("目标字数") }, modifier = Modifier.weight(1f))
                        OutlinedTextField(targetChapters, { targetChapters = it.filter(Char::isDigit) }, label = { Text("目标章节") }, modifier = Modifier.weight(1f))
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        OutlinedTextField(worldTone, { worldTone = it }, label = { Text("世界观基调") }, minLines = 2, modifier = Modifier.weight(1f))
                        OutlinedTextField(storyStructure, { storyStructure = it }, label = { Text("剧情结构") }, minLines = 2, modifier = Modifier.weight(1f))
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        OutlinedTextField(pacing, { pacing = it }, label = { Text("节奏控制") }, minLines = 2, modifier = Modifier.weight(1f))
                        OutlinedTextField(writingStyle, { writingStyle = it }, label = { Text("正文风格") }, minLines = 2, modifier = Modifier.weight(1f))
                    }
                    OutlinedTextField(
                        requirements,
                        { requirements = it },
                        label = { Text("必须保留（每行一项）") },
                        minLines = 2,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        avoid,
                        { avoid = it },
                        label = { Text("不要出现（每行一项）") },
                        minLines = 2,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }
    }
    Surface(color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
            TextButton(onClick = { showRoute = true }, modifier = Modifier.fillMaxWidth()) {
                Text(when {
                    route == CreationExecutionRoute.Pc -> "电脑模型 · 更换"
                    directApi != null -> "${directApi.displayName} · 更换模型"
                    else -> "选择 AI 模型"
                }, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Button(
                onClick = {
                    onStart(
                        CreationStartInput(
                            creationMode = creationMode,
                            brief = brief,
                            presetId = presetId,
                            themeId = themeId,
                            authorOutline = authorOutline,
                            genre = genre,
                            targetAudience = audience,
                            platform = platform,
                            targetWords = targetWords.toIntOrNull() ?: 600_000,
                            targetChapters = targetChapters.toIntOrNull() ?: 240,
                            worldTone = worldTone,
                            storyStructure = storyStructure,
                            pacing = pacing,
                            writingStyle = writingStyle,
                            specialRequirements = requirements.lines().filter(String::isNotBlank),
                            avoid = avoid.lines().filter(String::isNotBlank),
                            lockedRequirements = requirements.lines().filter(String::isNotBlank),
                        ),
                        route,
                    )
                },
                enabled = brief.isNotBlank() && !running &&
                    (route != CreationExecutionRoute.MobileKey || directApi != null),
                modifier = Modifier.fillMaxWidth().height(56.dp),
                shape = RoundedCornerShape(17.dp),
            ) {
                if (running) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                else Icon(Icons.Outlined.AutoAwesome, null)
                Spacer(Modifier.width(9.dp))
                Text(if (running) activity.ifBlank { "正在立项…" } else "开始立项", fontWeight = FontWeight.Bold)
            }
        }
    }
    }
    if (showDrafts) {
        ModalBottomSheet(onDismissRequest = { showDrafts = false },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
            Text("立项记录", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(20.dp))
            LazyColumn(contentPadding = PaddingValues(16.dp, 0.dp, 16.dp, 24.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                items(drafts, key = { it.string("id") }) { draft ->
                    DraftResumeCard(draft, stages) { showDrafts = false; onResume(it) }
                }
            }
        }
    }
    if (showRoute) {
        ModalBottomSheet(onDismissRequest = { showRoute = false }) {
            Text("使用哪个 AI？", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(20.dp))
            WorkspaceActionRow("手机 AI", directApi?.let { "${it.displayName} · ${it.model}" } ?: "配置 API 后可独立使用",
                Icons.Outlined.PhoneAndroid, {
                    showRoute = false
                    if (directApi == null) onConfigureApi() else route = CreationExecutionRoute.MobileKey
                })
            if (connection != null) WorkspaceActionRow("电脑模型", connection.gatewayName,
                Icons.Outlined.CloudQueue, { route = CreationExecutionRoute.Pc; showRoute = false })
            TextButton(onClick = { showRoute = false; onConfigureApi() }, modifier = Modifier.padding(16.dp)) { Text("管理手机 AI 配置") }
        }
    }
}

@Composable
private fun DraftResumeCard(
    draft: JsonObject,
    stages: List<Pair<String, String>>,
    onResume: (String) -> Unit,
) {
    val current = draft.string("current_stage")
    OutlinedCard(onClick = { onResume(draft.string("id")) }, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(17.dp)) {
        Row(Modifier.padding(15.dp), verticalAlignment = Alignment.CenterVertically) {
            Surface(color = MaterialTheme.colorScheme.primaryContainer, shape = RoundedCornerShape(12.dp)) {
                Icon(Icons.Outlined.AutoAwesome, null, tint = SimingCinnabar, modifier = Modifier.padding(10.dp))
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(draft.string("display_title").ifBlank { draft.string("user_brief") }.ifBlank { "未命名立项" }, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text("停在 ${stages.toMap()[current] ?: "AI 采访"} · 修订 ${draft.int("revision")}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Icon(Icons.AutoMirrored.Outlined.ArrowForward, null)
        }
    }
}

private fun ReplicaEntity.creationPayload(): JsonObject? = payloadJson?.let { raw ->
    runCatching { Json.parseToJsonElement(raw) as? JsonObject }.getOrNull()
}

private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
private fun JsonObject.linesText(name: String): String =
    (get(name) as? kotlinx.serialization.json.JsonArray)
        .orEmpty()
        .mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
        .joinToString("\n")
private fun JsonObject.int(name: String): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: 0
