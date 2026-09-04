package com.siming.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
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
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.material3.AssistChip
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.siming.mobile.data.agent.MobileConversationContextErrorCode
import com.siming.mobile.data.creation.CreationAgentTurnRecords
import com.siming.mobile.data.creation.CreationAgentProgressEvent
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

/** Chat-first creation UI matching the current desktop Creation Agent control plane. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun CreationConversationWorkspace(
    modifier: Modifier,
    session: JsonObject,
    stages: List<Pair<String, String>>,
    running: Boolean,
    activity: String,
    replyDelta: String,
    progressEvents: List<CreationAgentProgressEvent>,
    onBack: () -> Unit,
    onOpenDossier: () -> Unit,
    onSend: (String) -> Unit,
    onDiscard: () -> Unit,
    onConfigureApi: () -> Unit,
    onOpenProject: (String) -> Unit,
) {
    val draft = session.objectValue("draft")
    val messages = CreationAgentTurnRecords.displayMessages(session)
    var input by rememberSaveable(session.string("id")) { mutableStateOf("") }
    val projectId = session.string("created_project_id")
    val route = draft.string("execution_route")
    val host = draft.string("execution_host")
    val capacityUnknown = creationNeedsCapacityConfiguration(progressEvents)
    val conversationContext = creationConversationContextState(session, progressEvents)
    val lastAuthorRequest = CreationAgentTurnRecords.turns(session)
        .asReversed()
        .firstNotNullOfOrNull { it.string("user_content").takeIf(String::isNotBlank) }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp, 10.dp, 16.dp, 112.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回 AI 立项") }
                Column(Modifier.weight(1f)) {
                    Text(
                        session.string("display_title").ifBlank { session.string("user_brief") }.ifBlank { "新书立项" },
                        fontWeight = FontWeight.Bold,
                        fontSize = 21.sp,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        when {
                            route == "pc" -> "电脑线路 · PC 对话式 Creation Agent"
                            host == "gateway" -> "手机 Key · Gateway 执行 PC Creation Agent"
                            else -> "手机独立 · PC 同源 Creation Agent"
                        },
                        style = MaterialTheme.typography.labelSmall,
                        color = if (route == "pc") SimingBlue else SimingGreen,
                    )
                }
                IconButton(onClick = onOpenDossier, enabled = !running) {
                    Icon(Icons.Outlined.FolderOpen, "打开结构化建档页")
                }
                IconButton(onClick = onDiscard, enabled = !running) {
                    Icon(Icons.Outlined.DeleteOutline, "移除立项草稿")
                }
            }
        }

        item {
            Surface(
                color = Color(0xFFEAF4EF),
                shape = RoundedCornerShape(18.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.AutoAwesome, null, tint = SimingGreen)
                        Spacer(Modifier.width(8.dp))
                        Text("对话就是立项过程", fontWeight = FontWeight.Bold)
                    }
                    Text(
                        "每一轮 AI 都会先读取当前结构化资料，把你刚确认的事实立即写入，再基于真实数据决定下一步问题。没有“先采访完再统一生成”的阶段。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        if (capacityUnknown) {
            item {
                OutlinedCard(
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.error),
                    shape = RoundedCornerShape(18.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                        Text("模型上下文容量尚未配置", fontWeight = FontWeight.Bold)
                        Text(
                            "司命不会猜测模型窗口。配置上下文窗口、输出预留和安全余量后，再重新发送本轮要求。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        OutlinedButton(onClick = onConfigureApi, enabled = !running) {
                            Text("配置上下文容量")
                        }
                    }
                }
            }
        }

        conversationContext?.let { contextState ->
            item {
                CreationConversationContextCard(
                    state = contextState,
                    onConfigureApi = onConfigureApi,
                    onRetry = { lastAuthorRequest?.let(onSend) },
                    onNewCreation = onBack,
                    canRetry = !running && !lastAuthorRequest.isNullOrBlank(),
                )
            }
        }

        item {
            Text("实时立项资料", fontWeight = FontWeight.Bold, fontSize = 17.sp)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                stages.forEach { (stage, label) ->
                    val state = session.stageState(stage)
                    val status = state.string("status").ifBlank { "pending" }
                    val marker = when (status) {
                        "confirmed" -> "✓"
                        "generated" -> "•"
                        "stale", "conflict" -> "!"
                        else -> "○"
                    }
                    AssistChip(
                        onClick = { onSend("请读取并告诉我目前“$label”已经记录了什么，还缺什么；只在我给出新事实时再写入。") },
                        enabled = !running,
                        label = { Text("$marker $label") },
                    )
                }
            }
            Text(
                "修订 ${session.int("revision")} · 这些对象不再是强制顺序；你可以直接在聊天里跳到任意角色、设定、地点或大纲。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        item {
            OutlinedButton(
                onClick = onOpenDossier,
                enabled = !running,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Outlined.FolderOpen, null)
                Spacer(Modifier.width(8.dp))
                Text("打开 PC 同款结构化建档页", fontWeight = FontWeight.Bold)
            }
        }

        item { HorizontalDivider() }

        if (messages.isEmpty()) {
            item {
                AgentBubble(
                    role = "assistant",
                    content = "我已经建立立项会话。你继续像聊天一样说想法即可；我会边聊边把确定内容写入资料。",
                )
            }
        } else {
            items(messages, key = { it.string("id").ifBlank { "${it.string("role")}:${it.string("created_at")}:${it.string("content").hashCode()}" } }) { message ->
                AgentBubble(
                    role = message.string("role"),
                    content = message.string("content"),
                    progress = (message["progress_events"] as? JsonArray).orEmpty().mapNotNull { event ->
                        val item = event as? JsonObject ?: return@mapNotNull null
                        ProgressLine(item.string("type"), item.string("message"))
                    },
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
                        CircularProgressIndicator(Modifier.size(21.dp), strokeWidth = 2.dp, color = Color(0xFFFFC6B3))
                        Spacer(Modifier.width(11.dp))
                        Column {
                            Text("Creation Agent 正在工作", color = Color.White, fontWeight = FontWeight.Bold)
                            Text(
                                replyDelta.ifBlank { activity.ifBlank { "正在读取资料、执行工具并写入确定事实…" } },
                                color = Color.White.copy(alpha = 0.72f),
                                style = MaterialTheme.typography.bodySmall,
                            )
                            ProgressTimeline(
                                progressEvents
                                    .filter { it.type != "reply_delta" }
                                    .map { ProgressLine(it.type, it.message) },
                                dark = true,
                            )
                        }
                    }
                }
            }
        }

        if (projectId.isNotBlank()) {
            item {
                Button(onClick = { onOpenProject(projectId) }, modifier = Modifier.fillMaxWidth(), enabled = !running) {
                    Icon(Icons.Outlined.FolderOpen, null)
                    Spacer(Modifier.width(8.dp))
                    Text("打开已创建的正式作品")
                }
            }
        } else {
            item {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                    listOf(
                        "继续完善最关键的缺口",
                        "我想先聊主角和人物关系",
                        "我想先补世界观规则",
                        "检查一下现在是否可以建档",
                    ).forEach { prompt ->
                        AssistChip(onClick = { onSend(prompt) }, enabled = !running, label = { Text(prompt) })
                    }
                }
            }
            item {
                OutlinedCard(
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                    shape = RoundedCornerShape(20.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                        OutlinedTextField(
                            value = input,
                            onValueChange = { input = it },
                            label = { Text("继续和 AI 一起立项") },
                            placeholder = { Text("例如：主角叫陆糖，她最怕失去父亲；这一点先写进人物设定") },
                            minLines = 3,
                            maxLines = 8,
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(16.dp),
                            enabled = !running,
                        )
                        Button(
                            onClick = {
                                val text = input.trim()
                                if (text.isNotBlank()) {
                                    input = ""
                                    onSend(text)
                                }
                            },
                            enabled = input.isNotBlank() && !running,
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Icon(Icons.Outlined.AutoAwesome, null)
                            Spacer(Modifier.width(7.dp))
                            Text("发送给 Creation Agent", fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }

        item {
            Text(
                "你也可以打开结构化建档页，按 PC 相同的阶段逐项生成、编辑、确认并最终建档；对话与建档页始终读取同一份 V3 草稿。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

internal fun creationNeedsCapacityConfiguration(events: List<CreationAgentProgressEvent>): Boolean =
    events.asReversed().firstOrNull { it.type == "conversation_context" }
        ?.data
        ?.string("error_code") == MobileConversationContextErrorCode.CAPACITY_UNKNOWN

internal fun creationConversationContextState(
    session: JsonObject,
    liveEvents: List<CreationAgentProgressEvent>,
): MobileAssistantContextState? {
    val live = liveEvents.asReversed()
        .firstOrNull { it.type == "conversation_context" }
        ?.data
    val stored = CreationAgentTurnRecords.turns(session)
        .asReversed()
        .asSequence()
        .flatMap { turn ->
            (turn["progress_events"] as? JsonArray)
                .orEmpty()
                .asReversed()
                .asSequence()
        }
        .mapNotNull { it as? JsonObject }
        .firstOrNull { it.string("type") == "conversation_context" }
        ?.get("data") as? JsonObject
    val persistedState = CreationAgentTurnRecords.contextState(session)
    val persistedDetail = CreationAgentTurnRecords.checkpointDetail(session)
    val root = live ?: persistedState ?: stored ?: return null
    val state = root["context_state"] as? JsonObject ?: root
    val inlineDetail = root["checkpoint"] as? JsonObject
    val selectedCheckpointId = when (state.string("status")) {
        "ready" -> state.string("active_checkpoint_id")
        else -> state.string("latest_checkpoint_id").ifBlank {
            state.string("active_checkpoint_id")
        }
    }
    val matchingPersistedDetail = persistedDetail?.takeIf {
        selectedCheckpointId.isNotBlank() && it.string("id") == selectedCheckpointId
    }
    return runCatching {
        mobileAssistantContextStateFromJson(state, inlineDetail ?: matchingPersistedDetail)
    }.getOrNull()
}

@Composable
private fun CreationConversationContextCard(
    state: MobileAssistantContextState,
    onConfigureApi: () -> Unit,
    onRetry: () -> Unit,
    onNewCreation: () -> Unit,
    canRetry: Boolean,
) {
    var expanded by rememberSaveable(
        state.activeCheckpointId,
        state.latestCheckpointId,
        state.status,
    ) { mutableStateOf(false) }
    OutlinedCard(
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        shape = RoundedCornerShape(18.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Text(
                when (state.status) {
                    "compressing", "pending" -> "正在整理较早立项对话"
                    "failed" -> "立项对话上下文整理失败"
                    else -> "立项对话上下文"
                },
                fontWeight = FontWeight.Bold,
            )
            Text(
                state.detail.ifBlank { "完整聊天记录仍保留，模型只接收容量内的活动上下文。" },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (hasConversationContextDetails(state)) {
                TextButton(onClick = { expanded = !expanded }) {
                    Text(if (expanded) "收起详情" else "查看详情")
                    Icon(
                        if (expanded) Icons.Outlined.KeyboardArrowUp else Icons.Outlined.KeyboardArrowDown,
                        null,
                    )
                }
            }
            if (expanded) {
                HorizontalDivider()
                ConversationContextDetail(state)
            }
            if (requiresDirectContextCapacityConfiguration(state)) {
                OutlinedButton(onClick = onConfigureApi) { Text("配置上下文容量") }
            }
            if (state.status == "failed" && !requiresDirectContextCapacityConfiguration(state)) {
                Text(
                    "系统不会沿用失败的 checkpoint；重试会创建一个新的完整回合。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (state.retryable) {
                        Button(onClick = onRetry, enabled = canRetry) { Text("重试本轮要求") }
                    }
                    OutlinedButton(onClick = onNewCreation) { Text("新建立项") }
                }
            }
        }
    }
}

private data class ProgressLine(val type: String, val message: String)

@Composable
private fun AgentBubble(role: String, content: String, progress: List<ProgressLine> = emptyList()) {
    if (content.isBlank()) return
    val isUser = role == "user"
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        Surface(
            color = if (isUser) MaterialTheme.colorScheme.primaryContainer else Color.White,
            shape = RoundedCornerShape(18.dp),
            border = if (isUser) null else BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
            modifier = Modifier.fillMaxWidth(if (isUser) 0.88f else 0.96f),
        ) {
            Column(Modifier.padding(13.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(if (isUser) "你" else "司命", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, fontWeight = FontWeight.SemiBold)
                Text(content, lineHeight = 22.sp)
                if (!isUser) ProgressTimeline(progress)
            }
        }
    }
}

@Composable
private fun ProgressTimeline(lines: List<ProgressLine>, dark: Boolean = false) {
    val visibleLines = lines.filter { it.message.isNotBlank() }.takeLast(30)
    if (visibleLines.isEmpty()) return
    var expanded by rememberSaveable(visibleLines.lastOrNull()?.message) { mutableStateOf(false) }
    val textColor = if (dark) Color.White.copy(alpha = 0.76f) else MaterialTheme.colorScheme.onSurfaceVariant
    TextButton(
        onClick = { expanded = !expanded },
        contentPadding = PaddingValues(0.dp),
    ) {
        Text(
            if (expanded) "收起运行过程" else "运行过程（${visibleLines.size}）",
            color = textColor,
            style = MaterialTheme.typography.labelSmall,
        )
    }
    if (expanded) {
        Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
            visibleLines.forEach { line ->
                val marker = when (line.type) {
                    "tool_completed", "complete" -> "✓"
                    "error" -> "!"
                    else -> "•"
                }
                Text(
                    "$marker ${line.message}",
                    color = textColor,
                    style = MaterialTheme.typography.labelSmall,
                )
            }
        }
    }
}

private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
private fun JsonObject.int(name: String): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: 0
private fun JsonObject.stageState(stage: String): JsonObject = objectValue("draft").objectValue("stages").objectValue(stage)
