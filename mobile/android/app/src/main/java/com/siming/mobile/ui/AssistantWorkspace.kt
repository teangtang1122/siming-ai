package com.siming.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Devices
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.StopCircle
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.siming.mobile.data.AssistantModelRoute
import com.siming.mobile.data.agent.MobileConversationContextErrorCode
import kotlinx.coroutines.delay

internal data class AssistantQuickAction(
    val label: String,
    val prompt: String,
)

internal val assistantQuickActions = listOf(
    AssistantQuickAction("续写下一章", "用质量模式续写下一章。先读取与本章相关的角色、世界观和大纲信息，再开始写作；保持已有设定，不要自行改名或重写核心规则。"),
    AssistantQuickAction("规划后 3 章", "基于当前主线和已完成正文，规划接下来 3 章细纲。每章给出推进目标、冲突、关键角色和章末钩子。"),
    AssistantQuickAction("检查人物动机", "检查当前主要角色的目标、冲突和行为动机是否与最近正文一致；指出可能 OOC 的位置，并给出最小修改建议。"),
    AssistantQuickAction("检查世界观冲突", "结合现有世界观设定和最近正文，检查规则冲突、时间线矛盾和新增但未建档的设定。"),
)

@Composable
internal fun AssistantWorkspace(
    projectId: String,
    viewModel: MainViewModel,
    onConfigureDirectApi: () -> Unit,
) {
    var prompt by rememberSaveable { mutableStateOf("") }
    var submittedPrompt by rememberSaveable { mutableStateOf("") }
    val ui by viewModel.uiState
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    val directApi = ui.directApi
    var modelRoute by rememberSaveable { mutableStateOf("pc") }
    val listState = rememberLazyListState()

    LaunchedEffect(connection?.deviceId, directApi?.model) {
        modelRoute = when {
            connection == null && directApi != null -> "mobile"
            modelRoute == "mobile" && directApi == null -> "pc"
            else -> modelRoute
        }
    }

    val standaloneMobile = connection == null && directApi != null
    val gatewayMobile = connection != null && directApi != null && modelRoute == "mobile"
    val canUseAi = connection != null || directApi != null

    LaunchedEffect(ui.pendingAssistantRequest, canUseAi, ui.assistantRunning) {
        val queued = ui.pendingAssistantRequest
        if (!queued.isNullOrBlank() && canUseAi && !ui.assistantRunning) {
            val outgoing = viewModel.takePendingAssistantRequest() ?: return@LaunchedEffect
            submittedPrompt = outgoing
            viewModel.runAssistant(
                projectId,
                outgoing,
                if (modelRoute == "mobile") AssistantModelRoute.MobileKey else AssistantModelRoute.Pc,
            )
        }
    }

    LaunchedEffect(submittedPrompt, ui.assistantOutput, ui.assistantReasoning, ui.assistantActivity, ui.assistantRunning) {
        if (submittedPrompt.isNotBlank() || ui.assistantOutput.isNotBlank() || ui.assistantRunning) {
            runCatching { listState.animateScrollToItem(maxOf(0, listState.layoutInfo.totalItemsCount - 1)) }
        }
    }

    Column(Modifier.fillMaxSize().imePadding()) {
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth(),
            contentPadding = PaddingValues(start = 14.dp, top = 14.dp, end = 14.dp, bottom = 18.dp),
            verticalArrangement = Arrangement.spacedBy(11.dp),
        ) {
            item {
                Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    Text("AI 共创", style = MaterialTheme.typography.headlineSmall)
                    Text(
                        when {
                            standaloneMobile -> "手机独立 · ${directApi?.model.orEmpty()}"
                            gatewayMobile -> "PC 工作流 · 手机私有 Key"
                            connection != null -> "PC 工作流 · PC 已配置线路"
                            else -> "尚未配置 AI"
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            if (!canUseAi) {
                item {
                    StatusBanner(
                        icon = Icons.Outlined.CloudOff,
                        title = "先配置 AI 线路",
                        detail = "作品仍可离线编辑；到“设置”配置手机直连 API，或连接自己的 Gateway。",
                        warning = true,
                    )
                }
            } else if (standaloneMobile || gatewayMobile) {
                item {
                    StatusBanner(
                        icon = Icons.Outlined.PhoneAndroid,
                        title = if (standaloneMobile) "在手机执行完整工作区流程" else "PC 工作流使用手机模型",
                        detail = if (standaloneMobile) {
                            "使用与 PC 同源的提示词契约和结构化动作；需要落库的结果写入手机副本。"
                        } else {
                            "API Key 只在手机持久化；本轮加密交给自己的 Gateway，任务结束后释放。"
                        },
                    )
                }
            }

            if (canUseAi) {
                item {
                    LazyRow(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                        item {
                            AssistChip(
                                onClick = {
                                    submittedPrompt = ""
                                    viewModel.newAssistantConversation()
                                },
                                label = { Text("新对话") },
                                leadingIcon = { Icon(Icons.Outlined.Add, null, Modifier.size(16.dp)) },
                            )
                        }
                        items(ui.assistantConversations, key = { it.id }) { conversation ->
                            AssistChip(
                                onClick = {
                                    submittedPrompt = ""
                                    viewModel.loadAssistantConversation(projectId, conversation.id)
                                },
                                label = { Text(conversation.title, maxLines = 1) },
                                colors = AssistChipDefaults.assistChipColors(
                                    containerColor = if (conversation.id == ui.assistantConversationId) {
                                        MaterialTheme.colorScheme.primaryContainer
                                    } else Color.White,
                                ),
                            )
                        }
                    }
                }
            }

            if (
                submittedPrompt.isBlank() && ui.assistantOutput.isBlank() &&
                ui.assistantMessages.isEmpty() && !ui.assistantRunning
            ) {
                item { AssistantBubble("想从哪里开始？你可以直接描述任务，也可以先选一个常用动作。") }
                item {
                    Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                        Text("常用动作", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        assistantQuickActions.forEach { action ->
                            OutlinedButton(
                                onClick = {
                                    prompt = action.prompt
                                },
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Text(action.label, modifier = Modifier.weight(1f))
                                Text("填入", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
            }

            ui.assistantMessages.forEach { message ->
                item(key = message.id) {
                    if (message.role == "user") UserBubble(message.content)
                    else AssistantBubble(message.content)
                }
            }

            if (
                submittedPrompt.isNotBlank() &&
                ui.assistantMessages.lastOrNull { it.role == "user" }?.content != submittedPrompt
            ) {
                item { UserBubble(submittedPrompt) }
            }

            ui.assistantContextState?.let { contextState ->
                val retryPrompt = submittedPrompt.ifBlank {
                    ui.assistantMessages.lastOrNull { it.role == "user" }?.content.orEmpty()
                }
                item(
                    key = "assistant-context-${contextState.status}-${contextState.activeCheckpointId}-${contextState.latestCheckpointId}",
                ) {
                    ConversationContextNotice(
                        state = contextState,
                        onConfigureDirectApi = onConfigureDirectApi,
                        onRetry = {
                            if (retryPrompt.isNotBlank() && !ui.assistantRunning) {
                                viewModel.runAssistant(
                                    projectId,
                                    retryPrompt,
                                    if (modelRoute == "mobile") {
                                        AssistantModelRoute.MobileKey
                                    } else {
                                        AssistantModelRoute.Pc
                                    },
                                )
                            }
                        },
                        onNewConversation = {
                            submittedPrompt = ""
                            viewModel.newAssistantConversation()
                        },
                        canRetry = retryPrompt.isNotBlank() && !ui.assistantRunning,
                    )
                }
            }

            if (ui.assistantRunning) {
                item {
                    AssistantActivityBubble(
                        activity = ui.assistantActivity.ifBlank { "正在读取作品上下文并执行任务…" },
                    )
                }
            }

            if (ui.assistantReasoning.isNotBlank()) {
                item {
                    AssistantReasoningDisclosure(
                        text = ui.assistantReasoning,
                        streaming = ui.assistantRunning,
                        runKey = submittedPrompt,
                    )
                }
            }

            if (ui.assistantToolLog.isNotEmpty()) {
                item {
                    Card(
                        colors = CardDefaults.cardColors(containerColor = SimingPaperWarm),
                        modifier = Modifier.fillMaxWidth(0.92f),
                    ) {
                        Column(
                            Modifier.padding(horizontal = 13.dp, vertical = 10.dp),
                            verticalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            Text("工具执行记录", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                            ui.assistantToolLog.takeLast(8).forEach { entry ->
                                Text("• $entry", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
            }

            if (
                ui.assistantOutput.isNotBlank() &&
                (ui.assistantRunning || ui.assistantMessages.lastOrNull { it.role == "assistant" }?.content != ui.assistantOutput)
            ) {
                item {
                    AssistantBubble(ui.assistantOutput)
                }
            }
        }

        Surface(color = SimingPaperWarm, tonalElevation = 4.dp) {
            Column(
                modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 12.dp, vertical = 9.dp),
                verticalArrangement = Arrangement.spacedBy(7.dp),
            ) {
                if (connection != null && directApi != null) {
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        AssistChip(
                            onClick = { modelRoute = "pc" },
                            label = { Text("PC 线路") },
                            leadingIcon = { Icon(Icons.Outlined.Devices, null, Modifier.size(16.dp)) },
                            colors = AssistChipDefaults.assistChipColors(
                                containerColor = if (modelRoute == "pc") MaterialTheme.colorScheme.primaryContainer else Color.White,
                            ),
                        )
                        AssistChip(
                            onClick = { modelRoute = "mobile" },
                            label = { Text("手机 Key") },
                            leadingIcon = { Icon(Icons.Outlined.Key, null, Modifier.size(16.dp)) },
                            colors = AssistChipDefaults.assistChipColors(
                                containerColor = if (modelRoute == "mobile") MaterialTheme.colorScheme.primaryContainer else Color.White,
                            ),
                        )
                    }
                }

                Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = prompt,
                        onValueChange = { prompt = it },
                        placeholder = { Text("给项目助手发消息…") },
                        minLines = 1,
                        maxLines = 5,
                        modifier = Modifier.weight(1f),
                    )
                    if (ui.assistantRunning) {
                        IconButton(onClick = { viewModel.cancelAssistant(projectId) }, modifier = Modifier.size(50.dp)) {
                            Icon(Icons.Outlined.StopCircle, "停止", tint = MaterialTheme.colorScheme.error, modifier = Modifier.size(28.dp))
                        }
                    } else {
                        Button(
                            enabled = canUseAi && prompt.isNotBlank(),
                            onClick = {
                                val outgoing = prompt.trim()
                                if (outgoing.isBlank()) return@Button
                                submittedPrompt = outgoing
                                prompt = ""
                                viewModel.runAssistant(
                                    projectId,
                                    outgoing,
                                    if (modelRoute == "mobile") AssistantModelRoute.MobileKey else AssistantModelRoute.Pc,
                                )
                            },
                            contentPadding = PaddingValues(horizontal = 14.dp, vertical = 13.dp),
                        ) {
                            Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(19.dp))
                            Spacer(Modifier.width(5.dp))
                            Text("发送")
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ConversationContextNotice(
    state: MobileAssistantContextState,
    onConfigureDirectApi: () -> Unit,
    onRetry: () -> Unit,
    onNewConversation: () -> Unit,
    canRetry: Boolean,
) {
    var expanded by rememberSaveable(
        state.activeCheckpointId,
        state.latestCheckpointId,
        state.status,
    ) { mutableStateOf(false) }
    val title = when (state.status) {
        "pending", "compressing" -> "正在整理较早上下文"
        "ready" -> if (state.activeCheckpointId == null) "完整上下文在容量内" else "较早上下文已整理"
        "failed" -> "上下文整理失败"
        "cancelled" -> "上下文整理已取消"
        "stale", "superseded" -> "上下文需要重新整理"
        "syncing_transcript" -> "正在同步手机完整会话"
        "transcript_synced" -> "手机完整会话已同步"
        else -> "会话上下文"
    }
    Card(
        colors = CardDefaults.cardColors(containerColor = SimingPaperWarm),
        modifier = Modifier.fillMaxWidth(0.92f),
    ) {
        Column(
            Modifier.padding(horizontal = 13.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            Text(title, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
            state.detail.takeIf(String::isNotBlank)?.let {
                Text(it, style = MaterialTheme.typography.bodySmall)
            }
            val metrics = buildList {
                state.recentExactTurnCount?.let { add("最近原文 $it 轮") }
                state.capacityAssurance?.let { add("容量校验 $it") }
                if (state.originalHistoryTokens != null && state.activeHistoryTokens != null) {
                    add("历史 ${state.originalHistoryTokens} → ${state.activeHistoryTokens} tokens")
                }
            }
            if (metrics.isNotEmpty()) {
                Text(
                    metrics.joinToString(" · "),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            state.errorDetail?.takeIf(String::isNotBlank)?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            }
            if (hasConversationContextDetails(state)) {
                TextButton(onClick = { expanded = !expanded }) {
                    Text(if (expanded) "收起详情" else "查看详情")
                    Icon(
                        if (expanded) Icons.Outlined.KeyboardArrowUp else Icons.Outlined.KeyboardArrowDown,
                        null,
                        Modifier.size(18.dp),
                    )
                }
            }
            if (expanded) {
                HorizontalDivider()
                ConversationContextDetail(state)
            }
            if (requiresDirectContextCapacityConfiguration(state)) {
                OutlinedButton(onClick = onConfigureDirectApi) {
                    Text("配置上下文容量")
                }
            }
            if (state.status in setOf("failed", "cancelled", "stale", "superseded")) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (state.retryable) {
                        Button(onClick = onRetry, enabled = canRetry) {
                            Text("发送新消息重试")
                        }
                    }
                    OutlinedButton(onClick = onNewConversation) {
                        Text("新建对话")
                    }
                }
            }
        }
    }
}

@Composable
internal fun ConversationContextDetail(state: MobileAssistantContextState) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        ContextDetailRow("状态", conversationContextStatusLabel(state.status))
        ContextDetailRow("触发原因", conversationContextTriggerLabel(state.trigger))
        ContextDetailRow("来源范围", conversationContextRangeLabel(state))
        ContextDetailRow(
            "上下文 token",
            "原始 ${contextTokenLabel(state.originalHistoryTokens)} → " +
                "活动 ${contextTokenLabel(state.activeHistoryTokens)}" +
                (state.checkpointTokens?.let { " · checkpoint ${contextTokenLabel(it)}" } ?: ""),
        )
        ContextDetailRow(
            "保留原文",
            state.recentExactTurnCount?.let { "最近 $it 个完整回合" } ?: "未提供",
        )
        ContextDetailRow(
            "模型",
            listOfNotNull(state.provider, state.model).distinct().joinToString(":").ifBlank { "未提供" },
        )
        ContextDetailRow("容量保证", conversationContextAssuranceLabel(state.capacityAssurance))
        ContextDetailRow(
            "版本",
            "policy ${state.policyVersion ?: "—"} · schema ${state.schemaVersion ?: "—"}",
        )

        if (state.warnings.isNotEmpty()) {
            Text("整理警告", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
            state.warnings.forEach { warning ->
                Text("• $warning", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            }
        }

        Text("作者原话", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
        if (!state.checkpointDetailLoaded && state.activeCheckpointId != null) {
            Text(
                "checkpoint 详情尚未加载；不会据此判断没有作者原话。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else if (state.authorQuotes.isEmpty()) {
            Text(
                "此 checkpoint 没有需要逐字保留的作者原话。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            state.authorQuotes.forEach { quote ->
                Text(
                    "“${quote.exactQuote}”" +
                        (quote.purpose?.let { " · $it" } ?: "") +
                        (if (quote.superseded) " · 已被后续要求替代" else ""),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }

        Text("真实执行回执", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
        if (!state.checkpointDetailLoaded && state.activeCheckpointId != null) {
            Text(
                "checkpoint 详情尚未加载；不会据此判断没有执行回执。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else if (state.executionLedger.isEmpty()) {
            Text(
                "覆盖范围内没有需要保留的写入回执。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            state.executionLedger.forEach { entry ->
                val resources = entry.resourceIds.takeIf { it.isNotEmpty() }
                    ?.joinToString(prefix = " · ")
                    .orEmpty()
                Text(
                    "• ${entry.tool} · ${entry.status}" +
                        entry.detail.takeIf(String::isNotBlank)?.let { " · $it" }.orEmpty() +
                        resources,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

@Composable
private fun ContextDetailRow(label: String, value: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.bodySmall)
    }
}

internal fun hasConversationContextDetails(state: MobileAssistantContextState): Boolean =
    state.sourceRange != null || state.coveredSequenceRanges.isNotEmpty() ||
        state.originalHistoryTokens != null || state.activeHistoryTokens != null ||
        state.recentExactTurnCount != null || state.model != null || state.provider != null ||
        state.warnings.isNotEmpty() || state.authorQuotes.isNotEmpty() || state.executionLedger.isNotEmpty() ||
        state.errorCode != null

internal fun conversationContextRangeLabel(state: MobileAssistantContextState): String {
    val ranges = state.coveredSequenceRanges.ifEmpty { listOfNotNull(state.sourceRange) }
    if (ranges.isEmpty()) return "未提供"
    return ranges.joinToString("；") { range ->
        if (range.firstSequence != null && range.lastSequence != null) {
            "消息序号 ${range.firstSequence}–${range.lastSequence}" +
                (range.messageCount?.let { "（$it 条）" } ?: "")
        } else {
            range.messageCount?.let { "$it 条消息" } ?: "未提供"
        }
    }
}

internal fun conversationContextTriggerLabel(trigger: String?): String = when (trigger) {
    "projected_next_step_over_capacity" -> "下一模型步骤预计超过当前模型容量"
    "active_history_over_capacity" -> "活动对话历史超过当前模型容量"
    "tool_schema_growth_over_capacity" -> "开放工具后预计超过当前模型容量"
    "model_window_changed" -> "模型窗口变化后需要重新规划"
    "manual_rebuild" -> "作者主动要求重新整理"
    "within_capacity" -> "当前完整上下文仍在模型容量内"
    null, "" -> "未提供"
    else -> trigger
}

internal fun conversationContextStatusLabel(status: String): String = when (status) {
    "pending" -> "等待整理"
    "compressing" -> "正在整理"
    "ready" -> "可用"
    "failed" -> "失败"
    "cancelled" -> "已取消"
    "stale", "superseded" -> "需要重建"
    else -> status
}

internal fun conversationContextAssuranceLabel(assurance: String?): String = when (assurance) {
    "exact" -> "精确计数"
    "conservative" -> "保守上界"
    "unverified" -> "未验证容量（已启用安全兜底窗口）"
    null, "" -> "未提供"
    else -> assurance
}

private fun contextTokenLabel(value: Int?): String = value?.let { "%,d tokens".format(it) } ?: "未提供"

internal fun requiresDirectContextCapacityConfiguration(state: MobileAssistantContextState): Boolean =
    state.errorCode == MobileConversationContextErrorCode.CAPACITY_UNKNOWN
@Composable
private fun UserBubble(text: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
        Surface(
            color = MaterialTheme.colorScheme.primaryContainer,
            shape = RoundedCornerShape(topStart = 18.dp, topEnd = 5.dp, bottomStart = 18.dp, bottomEnd = 18.dp),
            modifier = Modifier.fillMaxWidth(0.86f),
        ) {
            Text(text, modifier = Modifier.padding(horizontal = 14.dp, vertical = 11.dp), style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable
private fun AssistantBubble(text: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Start) {
        Card(
            colors = CardDefaults.cardColors(containerColor = Color.White),
            shape = RoundedCornerShape(topStart = 5.dp, topEnd = 18.dp, bottomStart = 18.dp, bottomEnd = 18.dp),
            modifier = Modifier.fillMaxWidth(0.92f),
        ) {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("司命", style = MaterialTheme.typography.labelMedium, color = SimingCinnabar, fontWeight = FontWeight.SemiBold)
                Text(text, style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
}

@Composable
private fun AssistantActivityBubble(activity: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp)) {
        CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
        Text(
            activity,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
    }
}
@Composable
private fun AssistantReasoningDisclosure(
    text: String,
    streaming: Boolean,
    runKey: String,
) {
    var expanded by rememberSaveable(runKey) { mutableStateOf(streaming) }
    var visibleText by rememberSaveable(runKey) { mutableStateOf(if (streaming) "" else text) }

    LaunchedEffect(text, streaming, runKey) {
        if (!text.startsWith(visibleText)) {
            visibleText = if (streaming) "" else text
        }
        while (visibleText.length < text.length) {
            val codePoint = text.codePointAt(visibleText.length)
            val nextIndex = visibleText.length + Character.charCount(codePoint)
            visibleText = text.substring(0, nextIndex)
            delay(12)
        }
    }

    Card(
        onClick = { expanded = !expanded },
        colors = CardDefaults.cardColors(containerColor = SimingPaperWarm),
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth(0.92f),
    ) {
        Column(Modifier.padding(horizontal = 13.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(16.dp), tint = SimingCinnabar)
                    Text("模型思考摘要", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(
                        if (streaming) "实时生成" else "已完成",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (streaming) SimingCinnabar else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Icon(
                        if (expanded) Icons.Outlined.KeyboardArrowUp else Icons.Outlined.KeyboardArrowDown,
                        if (expanded) "收起模型思考摘要" else "展开模型思考摘要",
                        Modifier.size(18.dp),
                    )
                }
            }
            if (expanded) {
                Text(
                    "仅展示模型 API 实际返回的可见推理内容",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    visibleText + if (streaming || visibleText.length < text.length) "▍" else "",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
