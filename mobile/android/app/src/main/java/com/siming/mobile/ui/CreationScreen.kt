package com.siming.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.CloudQueue
import androidx.compose.material.icons.outlined.DataObject
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
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
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
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
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
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

    LaunchedEffect(connection?.deviceId) {
        if (connection != null) viewModel.refreshCreationDrafts()
    }

    when {
        ui.activeCreationId != null && active == null -> Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        active != null -> CreationConversationWorkspace(
            modifier = modifier,
            session = active,
            stages = stages,
            running = ui.creationRunning,
            activity = ui.creationActivity,
            onBack = viewModel::closeCreation,
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
                "接下来直接进入对话式立项：AI 每轮先读当前资料，把确定事实立即写入，再问最有价值的下一件事；角色、世界观和大纲不再有强制顺序。",
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

@OptIn(ExperimentalLayoutApi::class, ExperimentalSerializationApi::class)
@Composable
private fun CreationWorkspace(
    modifier: Modifier,
    session: JsonObject,
    stages: List<Pair<String, String>>,
    running: Boolean,
    activity: String,
    onBack: () -> Unit,
    onAnswer: (String, Boolean) -> Unit,
    onGenerate: (String, String) -> Unit,
    onConfirm: (String, String?) -> Unit,
    onArchive: () -> Unit,
    onDiscard: () -> Unit,
) {
    val prettyJson = remember { Json { prettyPrint = true; prettyPrintIndent = "  " } }
    val draft = session.objectValue("draft")
    val interview = draft.objectValue("interview")
    val route = draft.string("execution_route")
    val executionHost = draft.string("execution_host")
    val expanded = remember { mutableStateMapOf<String, Boolean>() }
    val instructions = remember { mutableStateMapOf<String, String>() }
    val editors = remember { mutableStateMapOf<String, String>() }
    val interviewTurn = (interview["history"] as? JsonArray)?.size ?: 0
    var answer by rememberSaveable(session.string("id"), interviewTurn) { mutableStateOf("") }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp, 10.dp, 16.dp, 112.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回 AI 立项") }
                Column(Modifier.weight(1f)) {
                    Text(session.string("display_title").ifBlank { "新书立项" }, fontWeight = FontWeight.Bold, fontSize = 21.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(
                        when {
                            route == "pc" -> "电脑线路 · PC 原生立项任务"
                            executionHost == "gateway" -> "手机 Key · Gateway 执行 PC 原生立项"
                            else -> "手机 Key · 本机执行同源 V3 契约"
                        },
                        style = MaterialTheme.typography.labelSmall,
                        color = if (route == "pc") SimingBlue else SimingGreen,
                    )
                }
                IconButton(onClick = onDiscard, enabled = !running) { Icon(Icons.Outlined.DeleteOutline, "移除立项草稿") }
            }
        }
        item {
            CreationProgressRail(session, stages)
        }
        if (running) {
            item {
                Surface(color = Color(0xFF272725), shape = RoundedCornerShape(18.dp), modifier = Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp, color = Color(0xFFFFC6B3))
                        Spacer(Modifier.width(12.dp))
                        Column {
                            Text("AI 正在工作", color = Color.White, fontWeight = FontWeight.Bold)
                            Text(activity.ifBlank { "正在整理立项上下文…" }, color = Color.White.copy(alpha = 0.72f), style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
        if (interview.string("status") !in setOf("completed", "skipped")) {
            item {
                InterviewCard(
                    interview = interview,
                    answer = answer,
                    onAnswerChange = { answer = it },
                    running = running,
                    onSubmit = {
                        onAnswer(answer, false)
                    },
                    onSkip = { onAnswer("", true) },
                    onStart = { onAnswer("", false) },
                )
            }
        } else {
            item {
                Surface(color = Color(0xFFEAF4EF), shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.CheckCircle, null, tint = SimingGreen)
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Text("AI 已理解立项上下文", fontWeight = FontWeight.SemiBold)
                            Text(interview.string("reason").ifBlank { "可以开始生成结构化创意方向。" }, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
        items(stages, key = { it.first }) { (stage, label) ->
            val state = session.stageState(stage)
            val status = state.string("status").ifBlank { "pending" }
            val data = state["data"] as? JsonObject
            val index = stages.indexOfFirst { it.first == stage }
            val previousReady = when {
                index == 0 -> interview.string("status") in setOf("completed", "skipped")
                stage == "final_review" -> session.stageState("macro_outline").string("status") == "confirmed"
                else -> session.stageState(stages[index - 1].first).string("status") == "confirmed"
            }
            val showBody = expanded[stage] == true || status == "generated"
            StageCard(
                number = index + 1,
                stage = stage,
                label = if (stage == "opening_outline") "$label（可选）" else label,
                status = status,
                data = data,
                warning = state.string("warning"),
                showBody = showBody,
                canGenerate = previousReady && !running,
                instruction = instructions[stage].orEmpty(),
                editor = editors[stage],
                onToggle = { expanded[stage] = !(expanded[stage] ?: false) },
                onInstruction = { instructions[stage] = it },
                onToggleEditor = {
                    if (editors.containsKey(stage)) {
                        editors.remove(stage)
                    } else {
                        editors[stage] = data?.let {
                            prettyJson.encodeToString(JsonObject.serializer(), it)
                        }.orEmpty()
                    }
                },
                onEditorChange = { editors[stage] = it },
                onGenerate = {
                    onGenerate(stage, instructions[stage].orEmpty())
                    instructions[stage] = ""
                },
                onConfirm = { onConfirm(stage, editors[stage]) },
            )
        }
        val review = session.stageData("final_review")
        val canArchive = session.stageState("final_review").string("status") in setOf("generated", "confirmed") &&
            (review["ready"] as? JsonPrimitive)?.booleanOrNull == true
        if (canArchive) {
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = Color(0xFF252522)),
                    shape = RoundedCornerShape(24.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("所有事实已经确认", color = Color.White, fontSize = 21.sp, fontWeight = FontWeight.Bold)
                        Text(
                            "现在才创建正式作品。立项草稿会转成作品资料、角色关系、世界设定和卷纲；只有已确认的前三章细纲才会一起入库。",
                            color = Color.White.copy(alpha = 0.72f),
                        )
                        Button(onClick = onArchive, enabled = !running, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                            Icon(Icons.Outlined.Archive, null)
                            Spacer(Modifier.width(8.dp))
                            Text("建立正式作品档案（细纲可稍后）", fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }
        item {
            Text(
                "为什么核心资料要确认？确认后的内容会成为下游 AI 的事实边界；前三章细纲是可选项，你仍可稍后在正式作品中继续完善。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 4.dp, vertical = 8.dp),
            )
        }
    }
}

@Composable
private fun CreationProgressRail(
    session: JsonObject,
    stages: List<Pair<String, String>>,
) {
    val confirmed = stages.count { session.stageState(it.first).string("status") == "confirmed" }
    Column(verticalArrangement = Arrangement.spacedBy(9.dp)) {
        Row(verticalAlignment = Alignment.Bottom) {
            Column(Modifier.weight(1f)) {
                Text("立项进度", fontWeight = FontWeight.Bold)
                Text("确认 $confirmed / ${stages.size}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text("V3 STRUCTURED DRAFT", fontSize = 10.sp, letterSpacing = 1.2.sp, color = SimingCinnabar)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(5.dp), modifier = Modifier.fillMaxWidth()) {
            stages.forEach { (stage, _) ->
                val status = session.stageState(stage).string("status")
                Box(
                    Modifier
                        .weight(1f)
                        .height(6.dp)
                        .background(
                            when (status) {
                                "confirmed" -> SimingGreen
                                "generated" -> SimingCinnabar
                                else -> MaterialTheme.colorScheme.outlineVariant
                            },
                            CircleShape,
                        ),
                )
            }
        }
    }
}

@Composable
private fun InterviewCard(
    interview: JsonObject,
    answer: String,
    onAnswerChange: (String) -> Unit,
    running: Boolean,
    onSubmit: () -> Unit,
    onSkip: () -> Unit,
    onStart: () -> Unit,
) {
    val pending = interview["pending_question"] as? JsonObject
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xFFFFFBF2)),
        border = BorderStroke(1.dp, Color(0xFFE8D7C4)),
        shape = RoundedCornerShape(22.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Surface(color = SimingCinnabar, shape = CircleShape) {
                    Icon(Icons.Outlined.AutoAwesome, null, tint = Color.White, modifier = Modifier.padding(9.dp).size(18.dp))
                }
                Spacer(Modifier.width(10.dp))
                Column {
                    Text("AI 策划编辑", fontWeight = FontWeight.Bold)
                    Text("一次只问真正会改变故事的问题", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (interview.string("status") == "failed") {
                Text("刚才的回答已经保存", fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                Text(
                    interview.string("error_message").ifBlank { "模型没有完成本轮判断。" },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    interview.string("next_action").ifBlank { "请发送“继续”重试。" },
                    style = MaterialTheme.typography.bodySmall,
                    color = SimingCinnabar,
                )
                Button(onClick = onStart, enabled = !running, modifier = Modifier.fillMaxWidth()) {
                    Text("继续判断")
                }
            } else if (pending == null) {
                Text("我会先读你的完整构想，再决定是追问一个关键分岔，还是直接开始生成。", lineHeight = 23.sp)
                Button(onClick = onStart, enabled = !running, modifier = Modifier.fillMaxWidth()) {
                    Text("开始 AI 采访")
                }
            } else {
                Text(pending.string("question"), fontSize = 19.sp, fontWeight = FontWeight.SemiBold, lineHeight = 27.sp)
                if (pending.string("purpose").isNotBlank()) {
                    Text("为什么问：${pending.string("purpose")}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                OutlinedTextField(
                    value = answer,
                    onValueChange = onAnswerChange,
                    label = { Text("像聊天一样回答") },
                    placeholder = { Text("不需要术语，把你真正想要的感觉说出来即可") },
                    minLines = 3,
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(16.dp),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    OutlinedButton(onClick = onSkip, enabled = !running, modifier = Modifier.weight(1f)) {
                        Text("信息够了，生成")
                    }
                    Button(onClick = onSubmit, enabled = answer.isNotBlank() && !running, modifier = Modifier.weight(1f)) {
                        Text("回答并继续")
                    }
                }
            }
        }
    }
}

@Composable
private fun StageCard(
    number: Int,
    stage: String,
    label: String,
    status: String,
    data: JsonObject?,
    warning: String,
    showBody: Boolean,
    canGenerate: Boolean,
    instruction: String,
    editor: String?,
    onToggle: () -> Unit,
    onInstruction: (String) -> Unit,
    onToggleEditor: () -> Unit,
    onEditorChange: (String) -> Unit,
    onGenerate: () -> Unit,
    onConfirm: () -> Unit,
) {
    val statusColor = when (status) {
        "confirmed" -> SimingGreen
        "generated" -> SimingCinnabar
        "stale", "conflict" -> MaterialTheme.colorScheme.error
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    OutlinedCard(
        modifier = Modifier.fillMaxWidth(),
        border = BorderStroke(1.dp, if (status == "generated") SimingCinnabar.copy(alpha = 0.55f) else MaterialTheme.colorScheme.outlineVariant),
        colors = CardDefaults.outlinedCardColors(containerColor = Color.White),
        shape = RoundedCornerShape(20.dp),
    ) {
        Column {
            Row(
                modifier = Modifier.fillMaxWidth().clickable(onClick = onToggle).padding(15.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Surface(color = statusColor.copy(alpha = 0.12f), shape = CircleShape) {
                    Box(Modifier.size(36.dp), contentAlignment = Alignment.Center) {
                        if (status == "confirmed") Icon(Icons.Outlined.CheckCircle, null, tint = statusColor, modifier = Modifier.size(20.dp))
                        else Text(number.toString(), color = statusColor, fontWeight = FontWeight.Bold)
                    }
                }
                Spacer(Modifier.width(11.dp))
                Column(Modifier.weight(1f)) {
                    Text(label, fontWeight = FontWeight.Bold)
                    Text(
                        when (status) {
                            "confirmed" -> "已确认 · 成为下游事实"
                            "generated" -> if (warning.isBlank()) "AI 已生成 · 等你审阅" else "已自动恢复 · 请重点审阅"
                            "stale" -> "上游有变化 · 建议重新生成"
                            else -> if (canGenerate) "已就绪 · 交给 AI 生成" else "等待上一步确认"
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = statusColor,
                    )
                }
                Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(17.dp))
            }
            if (showBody) {
                HorizontalDivider()
                Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (warning.isNotBlank()) {
                        Surface(
                            color = Color(0xFFFFF4E5),
                            shape = RoundedCornerShape(14.dp),
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text(
                                warning,
                                color = Color(0xFF7A4B17),
                                style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.padding(12.dp),
                            )
                        }
                    }
                    if (data != null) {
                        StageHumanSummary(stage, data)
                        TextButton(onClick = onToggleEditor) {
                            Icon(Icons.Outlined.DataObject, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text(if (editor == null) "查看并编辑结构化数据" else "收起结构化数据")
                        }
                        if (editor != null) {
                            OutlinedTextField(
                                value = editor,
                                onValueChange = onEditorChange,
                                label = { Text("PC V3 JSON") },
                                textStyle = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                                minLines = 8,
                                maxLines = 22,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                    if (status != "confirmed" && canGenerate) {
                        OutlinedTextField(
                            value = instruction,
                            onValueChange = onInstruction,
                            label = { Text(if (data == null) "给 AI 的补充要求（可选）" else "哪里需要调整？") },
                            placeholder = { Text("例如：冲突更贴近日常，不要依赖超能力") },
                            minLines = 2,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        if (data == null) {
                            Button(onClick = onGenerate, modifier = Modifier.fillMaxWidth()) {
                                Icon(Icons.Outlined.AutoAwesome, null)
                                Spacer(Modifier.width(7.dp))
                                Text("让 AI 生成$label")
                            }
                        } else {
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                                OutlinedButton(onClick = onGenerate, modifier = Modifier.weight(1f)) {
                                    Icon(Icons.Outlined.Refresh, null, Modifier.size(18.dp))
                                    Spacer(Modifier.width(5.dp))
                                    Text("按意见调整")
                                }
                                Button(onClick = onConfirm, modifier = Modifier.weight(1f)) {
                                    Icon(Icons.Outlined.CheckCircle, null, Modifier.size(18.dp))
                                    Spacer(Modifier.width(5.dp))
                                    Text("确认并继续")
                                }
                            }
                        }
                    }
                    if (status == "confirmed") {
                        Text(
                            "需要改变时仍可让 AI 定向调整，或直接编辑 V3 数据；下游内容会被标为需要复核。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        OutlinedTextField(
                            value = instruction,
                            onValueChange = onInstruction,
                            label = { Text("想改变什么？") },
                            placeholder = { Text("例如：保留主角名字，但把关系改成亦敌亦友") },
                            minLines = 2,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                            if (editor != null) {
                                OutlinedButton(onClick = onConfirm, modifier = Modifier.weight(1f)) {
                                    Icon(Icons.Outlined.DataObject, null, Modifier.size(18.dp))
                                    Spacer(Modifier.width(5.dp))
                                    Text("保存结构修改")
                                }
                            }
                            Button(
                                onClick = onGenerate,
                                enabled = instruction.isNotBlank(),
                                modifier = Modifier.weight(1f),
                            ) {
                                Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(18.dp))
                                Spacer(Modifier.width(5.dp))
                                Text("让 AI 定向调整")
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun StageHumanSummary(stage: String, data: JsonObject) {
    val lines = when (stage) {
        "concepts" -> {
            val card = (data["options"] as? JsonArray)?.firstOrNull() as? JsonObject
            listOfNotNull(
                card?.string("title")?.takeIf(String::isNotBlank),
                card?.string("logline")?.takeIf(String::isNotBlank),
                card?.string("core_conflict")?.takeIf(String::isNotBlank)?.let { "核心冲突：$it" },
            )
        }
        "world_style" -> listOf(
            "文风：${data.string("writing_style")}",
            "世界基调：${data.string("world_tone")}",
            "世界设定 ${(data["worldbuilding"] as? JsonArray)?.size ?: 0} 条",
        )
        "characters" -> listOf(
            "角色 " + (data["characters"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.string("name") }.joinToString("、"),
            "关系 ${(data["relationships"] as? JsonArray)?.size ?: 0} 条",
        )
        "locations" -> listOf(
            "地点 / 势力 " + (data["entries"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.string("title") }.take(6).joinToString("、"),
            "结构关系 ${(data["relations"] as? JsonArray)?.size ?: 0} 条",
        )
        "macro_outline" -> listOf(
            data.string("story_overview"),
            "核心冲突：${data.string("core_conflict")}",
            "分卷 ${(data["volumes"] as? JsonArray)?.size ?: 0} 卷",
        )
        "opening_outline" -> listOf(
            (data["chapters"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.string("title") }.joinToString(" · "),
            "场景节点 ${(data["sections"] as? JsonArray)?.size ?: 0} 个",
        )
        "final_review" -> listOf(
            if ((data["ready"] as? JsonPrimitive)?.booleanOrNull == true) "审阅通过：可以正式建档" else "还有阻塞项需要处理",
            (data["blocking"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull }.joinToString("；"),
        )
        else -> emptyList()
    }.filter(String::isNotBlank)
    Surface(color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f), shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(13.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            lines.forEachIndexed { index, line ->
                Text(line, fontWeight = if (index == 0) FontWeight.SemiBold else FontWeight.Normal, style = if (index == 0) MaterialTheme.typography.bodyLarge else MaterialTheme.typography.bodyMedium)
            }
        }
    }
}

private fun ReplicaEntity.creationPayload(): JsonObject? = payloadJson?.let { raw ->
    runCatching { Json.parseToJsonElement(raw) as? JsonObject }.getOrNull()
}

private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
private fun JsonObject.int(name: String): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: 0
private fun JsonObject.stageState(stage: String): JsonObject =
    objectValue("draft").objectValue("stages").objectValue(stage)
private fun JsonObject.stageData(stage: String): JsonObject =
    stageState(stage)["data"] as? JsonObject ?: JsonObject(emptyMap())
