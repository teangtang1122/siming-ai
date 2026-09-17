package com.siming.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberCoroutineScope
import kotlinx.coroutines.launch
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
    onOpenDossier: (String?) -> Unit,
    onSend: (String) -> Unit,
    onDiscard: () -> Unit,
    onContinueOnPhone: () -> Unit,
    onConfigureApi: () -> Unit,
    onOpenProject: (String) -> Unit,
) {
    val draft = session.objectValue("draft")
    val messages = CreationAgentTurnRecords.displayMessages(session)
    var input by rememberSaveable(session.string("id")) { mutableStateOf("") }
    val projectId = session.string("created_project_id")
    val route = draft.string("execution_route")
    val capacityUnknown = creationNeedsCapacityConfiguration(progressEvents)
    val conversationContext = creationConversationContextState(session, progressEvents)
    val lastAuthorRequest = CreationAgentTurnRecords.turns(session)
        .asReversed()
        .firstNotNullOfOrNull { it.string("user_content").takeIf(String::isNotBlank) }

    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    var showDetails by rememberSaveable(session.string("id")) { mutableStateOf(false) }
    val confirmedCount = stages.count { session.stageState(it.first).string("status") == "confirmed" }
    LaunchedEffect(session.string("id"), messages.size, running) {
        // Scroll on a new turn, never on each token while the author reads history.
        listState.scrollToItem((listState.layoutInfo.totalItemsCount - 1).coerceAtLeast(0))
    }
    Column(modifier.fillMaxSize().imePadding()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回立项列表") }
            Column(Modifier.weight(1f)) {
                Text(session.string("display_title").ifBlank { "新书立项" },
                    fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text("已确认 $confirmedCount / ${stages.size} 项 · ${if (route == "pc") "电脑线路" else "手机独立"}",
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            TextButton(onClick = { onOpenDossier(null) }) { Text("资料") }
            IconButton(onClick = { showDetails = !showDetails }) {
                Icon(if (showDetails) Icons.Outlined.KeyboardArrowUp else Icons.Outlined.KeyboardArrowDown, "立项详情与管理")
            }
        }
        LazyRow(contentPadding = PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            items(stages, key = { it.first }) { (stage, label) ->
                val status = when (session.stageState(stage).string("status")) {
                    "confirmed" -> "已确认"
                    "generated" -> "待确认"
                    "stale", "conflict" -> "待复核"
                    else -> "待完善"
                }
                AssistChip(onClick = { onOpenDossier(stage) }, label = { Text("$label · $status") })
            }
        }
        HorizontalDivider()
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth(),
            contentPadding = PaddingValues(16.dp, 12.dp, 16.dp, 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (showDetails) {
                item {
                    ContextInspectorButton(kind = "creation_session", scopeId = session.string("id"))
                    Text("资料已保存在当前立项中。点上方阶段可查看、编辑和确认。",
                        style = MaterialTheme.typography.bodySmall)
                    TextButton(onClick = onDiscard, enabled = !running) { Text("移除立项草稿") }
                }
            }
            if (route == "pc" && projectId.isBlank()) {
                item {
                    OutlinedButton(onClick = onContinueOnPhone, enabled = !running) { Text("转为手机独立立项") }
                }
            }
            if (capacityUnknown) {
                item { OutlinedButton(onClick = onConfigureApi, enabled = !running) { Text("配置模型上下文容量") } }
            }
            conversationContext?.let { state ->
                if (showDetails || state.status in setOf("failed", "compressing", "pending")) {
                    item {
                        CreationConversationContextCard(state, onConfigureApi,
                            onRetry = { lastAuthorRequest?.let(onSend) }, onNewCreation = onBack,
                            canRetry = !running && !lastAuthorRequest.isNullOrBlank())
                    }
                }
            }
            if (messages.isEmpty()) {
                item {
                    AgentBubble("assistant", "告诉我你的故事想法。生成的资料会先保存为草稿，等你审阅确认。")
                }
            } else {
                items(messages, key = { it.string("id").ifBlank { "${it.string("role")}:${it.string("created_at")}:${it.string("content").hashCode()}" } }) { message ->
                    AgentBubble(role = message.string("role"), content = message.string("content"),
                        progress = (message["progress_events"] as? JsonArray).orEmpty().mapNotNull { event ->
                            val line = event as? JsonObject ?: return@mapNotNull null
                            ProgressLine(line.string("type"), line.string("message"))
                        })
                    if (showDetails && message.string("role") == "assistant") {
                        ContextInspectorButton(kind = "creation_session", scopeId = session.string("id"),
                            correlationId = message.string("id").removeSuffix(":assistant"), label = "查看本轮调用")
                    }
                }
            }
            if (running && replyDelta.isNotBlank()) {
                item { AgentBubble("assistant", replyDelta) }
            }
            if (projectId.isNotBlank()) {
                item {
                    Button(onClick = { onOpenProject(projectId) }, modifier = Modifier.fillMaxWidth()) {
                        Text("打开正式作品")
                    }
                }
            }
        }
        if (listState.canScrollForward) {
            TextButton(onClick = { scope.launch { listState.animateScrollToItem((listState.layoutInfo.totalItemsCount - 1).coerceAtLeast(0)) } },
                modifier = Modifier.align(Alignment.CenterHorizontally)) { Text("回到最新消息 ↓") }
        }
        if (running) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(10.dp))
                Text(activity.ifBlank { "正在处理，请稍候…" }, style = MaterialTheme.typography.bodySmall,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
        if (projectId.isBlank()) {
            Surface(tonalElevation = 2.dp) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Bottom) {
                    OutlinedTextField(value = input, onValueChange = { input = it },
                        placeholder = { Text("说说你的想法…") }, minLines = 1, maxLines = 4,
                        modifier = Modifier.weight(1f), shape = RoundedCornerShape(18.dp))
                    Button(onClick = {
                        val message = input.trim()
                        if (message.isNotBlank()) { input = ""; onSend(message) }
                    }, enabled = input.isNotBlank() && !running,
                        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 16.dp)) {
                        Text("发送")
                    }
                }
            }
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
