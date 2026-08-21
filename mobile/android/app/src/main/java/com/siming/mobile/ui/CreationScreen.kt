package com.siming.mobile.ui

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
        pcContract.stageOrder
            .filterNot { it == "constraints" }
            .map { it to pcContract.stageLabels.getValue(it) }
    }
    val drafts by viewModel.creationDrafts.collectAsStateWithLifecycle()
    val ui by viewModel.uiState
    val active = drafts.firstOrNull { it.entityId == ui.activeCreationId }?.creationPayload()
    var showDossier by rememberSaveable(ui.activeCreationId) { mutableStateOf(false) }

    LaunchedEffect(connection?.deviceId) {
        if (connection != null) viewModel.refreshCreationDrafts()
    }

    when {
        ui.activeCreationId != null && active == null -> Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        active != null && showDossier -> CreationDossierWorkspace(
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
            onBack = viewModel::closeCreation,
            onOpenDossier = { showDossier = true },
            onSend = { message -> viewModel.sendCreationMessage(active.string("id"), message) },
            onDiscard = { viewModel.discardCreation(active.string("id")) },
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

@OptIn(ExperimentalLayoutApi::class)
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
        mutableStateOf(if (connection != null) CreationExecutionRoute.Pc else CreationExecutionRoute.MobileKey)
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

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(18.dp, 18.dp, 18.dp, 110.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { CreationHero() }
        if (drafts.isNotEmpty()) {
            item {
                Text("继续上次立项", fontWeight = FontWeight.Bold, fontSize = 18.sp)
                Text(
                    "未完成的思路不会丢；回到 AI 上次停下的位置。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            items(drafts.take(3), key = { it.string("id") }) { draft ->
                DraftResumeCard(draft, stages, onResume)
            }
            item { HorizontalDivider(Modifier.padding(vertical = 4.dp)) }
        }
        item {
            Text("AI 怎么参与", fontWeight = FontWeight.Bold, fontSize = 18.sp)
            Text(
                "先选合作方式。这里只决定 AI 如何尊重你的素材，不是让你填完一张长表。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
                ChoiceCard(
                    selected = creationMode == "author_led",
                    title = "按我的设定",
                    detail = "AI 整理、补空白，不改写专名和已定方向",
                    icon = Icons.Outlined.Lock,
                    modifier = Modifier.weight(1f),
                    onClick = { creationMode = "author_led" },
                )
                ChoiceCard(
                    selected = creationMode == "explore",
                    title = "帮我探索",
                    detail = "AI 边聊边写入资料，与我一起找到可持续的创意",
                    icon = Icons.Outlined.AutoAwesome,
                    modifier = Modifier.weight(1f),
                    onClick = { creationMode = "explore" },
                )
            }
        }
        item {
            OutlinedTextField(
                value = brief,
                onValueChange = { brief = it },
                label = { Text(if (creationMode == "author_led") "把已有想法告诉 AI" else "从一个念头开始") },
                placeholder = {
                    Text(
                        if (creationMode == "author_led") {
                            "例：主角能看见别人寿命，但每次救人都会忘掉一段记忆……"
                        } else {
                            "例：我想写都市悬疑，核心是亲密关系里的信任"
                        }
                    )
                },
                minLines = 5,
                maxLines = 10,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(18.dp),
            )
        }
        item {
            TextButton(onClick = { advanced = !advanced }) {
                Icon(Icons.Outlined.Edit, null, Modifier.size(18.dp))
                Spacer(Modifier.width(7.dp))
                Text(if (advanced) "收起可选约束" else "我还想锁定篇幅、平台或避雷项")
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
        item {
            Text("选择 AI 线路", fontWeight = FontWeight.Bold, fontSize = 18.sp)
            Text(
                "有 Gateway 时可随时选电脑配置或手机 Key；两条线路使用同一套对话式 Creation Agent 提示词、工具和建档结构。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(9.dp)) {
                if (connection != null) {
                    RouteCard(
                        selected = route == CreationExecutionRoute.Pc,
                        icon = Icons.Outlined.CloudQueue,
                        title = "使用电脑线路",
                        detail = "${connection.gatewayName} · 直接复用 PC 模型配置与可恢复任务",
                        badge = "PC 同一 API",
                        onClick = { route = CreationExecutionRoute.Pc },
                    )
                }
                RouteCard(
                    selected = route == CreationExecutionRoute.MobileKey,
                    icon = if (directApi == null) Icons.Outlined.Key else Icons.Outlined.PhoneAndroid,
                    title = "使用手机保存的 Key",
                    detail = directApi?.let {
                        if (connection == null) {
                            "${it.displayName} · ${it.model} · 手机直接调用"
                        } else {
                            "${it.displayName} · ${it.model} · 单次加密后由 PC 原生立项引擎执行"
                        }
                    }
                        ?: "还没有配置；配置后无需 Gateway 也能完整立项",
                    badge = when {
                        directApi == null -> "去配置"
                        connection != null -> "PC 原生流程"
                        else -> "PC 同源 Agent"
                    },
                    onClick = {
                        if (directApi == null) onConfigureApi() else route = CreationExecutionRoute.MobileKey
                    },
                )
            }
        }
        item {
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
                Text(if (running) activity.ifBlank { "AI 正在进入故事…" } else "让 AI 开始立项", fontWeight = FontWeight.Bold)
            }
        }
        item {
            Text(
                "接下来可在对话与结构化建档页之间随时切换：聊天负责补想法，建档页负责像 PC 一样生成、编辑、确认每个阶段并建立正式作品。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CreationHero() {
    Card(
        colors = CardDefaults.cardColors(containerColor = Color.Transparent),
        shape = RoundedCornerShape(28.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Box(
            Modifier
                .background(
                    Brush.linearGradient(
                        listOf(Color(0xFF20201F), Color(0xFF49332D), Color(0xFF873D35)),
                    ),
                )
                .fillMaxWidth()
                .padding(22.dp),
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Surface(color = Color.White.copy(alpha = 0.12f), shape = RoundedCornerShape(20.dp)) {
                    Text(
                        "AI CO-AUTHOR STUDIO",
                        color = Color(0xFFFFD8C7),
                        fontSize = 11.sp,
                        letterSpacing = 1.6.sp,
                        modifier = Modifier.padding(horizontal = 11.dp, vertical = 6.dp),
                    )
                }
                Text("一句话，和 AI 一起立项", color = Color.White, fontSize = 29.sp, fontWeight = FontWeight.Bold)
                Text(
                    "不是空白纸，也不是手写建档。司命会像 PC 端一样边聊边读取和写入作品资料；你每确认一个事实，它就立即进入结构化草稿。",
                    color = Color.White.copy(alpha = 0.82f),
                    lineHeight = 22.sp,
                )
                FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                    listOf("即时写入", "按需追问", "任意顺序", "一键建档").forEach {
                        Surface(color = Color.White.copy(alpha = 0.1f), shape = RoundedCornerShape(12.dp)) {
                            Text(it, color = Color.White, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 9.dp, vertical = 5.dp))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ChoiceCard(
    selected: Boolean,
    title: String,
    detail: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    modifier: Modifier,
    onClick: () -> Unit,
) {
    OutlinedCard(
        onClick = onClick,
        modifier = modifier,
        colors = CardDefaults.outlinedCardColors(
            containerColor = if (selected) MaterialTheme.colorScheme.primaryContainer else Color.White,
        ),
        border = BorderStroke(1.5.dp, if (selected) SimingCinnabar else MaterialTheme.colorScheme.outlineVariant),
        shape = RoundedCornerShape(18.dp),
    ) {
        Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(icon, null, tint = if (selected) SimingCinnabar else MaterialTheme.colorScheme.onSurfaceVariant)
            Text(title, fontWeight = FontWeight.Bold)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun RouteCard(
    selected: Boolean,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    detail: String,
    badge: String,
    onClick: () -> Unit,
) {
    OutlinedCard(
        onClick = onClick,
        colors = CardDefaults.outlinedCardColors(containerColor = if (selected) Color(0xFFF2F7F4) else Color.White),
        border = BorderStroke(1.5.dp, if (selected) SimingGreen else MaterialTheme.colorScheme.outlineVariant),
        shape = RoundedCornerShape(17.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Surface(color = if (selected) SimingGreen else MaterialTheme.colorScheme.surfaceVariant, shape = CircleShape) {
                Icon(icon, null, tint = if (selected) Color.White else MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(9.dp).size(20.dp))
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(title, fontWeight = FontWeight.SemiBold)
                Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Surface(color = if (selected) SimingGreen.copy(alpha = 0.12f) else MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(10.dp)) {
                Text(badge, color = if (selected) SimingGreen else MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 11.sp, modifier = Modifier.padding(horizontal = 8.dp, vertical = 5.dp))
            }
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
