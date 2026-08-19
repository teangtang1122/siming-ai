package com.siming.mobile.ui

import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Devices
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material.icons.outlined.Save
import androidx.compose.material.icons.outlined.StopCircle
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
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

internal data class AssistantQuickAction(
    val label: String,
    val scope: String,
    val prompt: String,
)

internal val assistantQuickActions = listOf(
    AssistantQuickAction("续写下一章", "project", "用质量模式续写下一章。先读取与本章相关的角色、世界观和大纲信息，再开始写作；保持已有设定，不要自行改名或重写核心规则。"),
    AssistantQuickAction("规划后 3 章", "outline", "基于当前主线和已完成正文，规划接下来 3 章细纲。每章给出推进目标、冲突、关键角色和章末钩子。"),
    AssistantQuickAction("检查人物动机", "characters", "检查当前主要角色的目标、冲突和行为动机是否与最近正文一致；指出可能 OOC 的位置，并给出最小修改建议。"),
    AssistantQuickAction("检查世界观冲突", "worldbuilding", "结合现有世界观设定和最近正文，检查规则冲突、时间线矛盾和新增但未建档的设定。"),
)

@Composable
internal fun AssistantWorkspace(projectId: String, viewModel: MainViewModel) {
    var prompt by rememberSaveable { mutableStateOf("") }
    var submittedPrompt by rememberSaveable { mutableStateOf("") }
    var scope by rememberSaveable { mutableStateOf("project") }
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

    LaunchedEffect(submittedPrompt, ui.assistantOutput, ui.assistantActivity, ui.assistantRunning) {
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

            if (submittedPrompt.isBlank() && ui.assistantOutput.isBlank() && !ui.assistantRunning) {
                item { AssistantBubble("想从哪里开始？你可以直接描述任务，也可以先选一个常用动作。") }
                item {
                    Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                        Text("常用动作", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        assistantQuickActions.forEach { action ->
                            OutlinedButton(
                                onClick = {
                                    scope = action.scope
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

            if (submittedPrompt.isNotBlank()) {
                item { UserBubble(submittedPrompt) }
            }

            if (ui.assistantRunning) {
                item {
                    AssistantActivityBubble(
                        activity = ui.assistantActivity.ifBlank { "正在读取作品上下文并执行任务…" },
                    )
                }
            }

            if (ui.assistantOutput.isNotBlank()) {
                item {
                    Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                        AssistantBubble(ui.assistantOutput)
                        if (standaloneMobile && !ui.assistantRunning) {
                            OutlinedButton(onClick = { viewModel.saveAssistantAsChapter(projectId) }) {
                                Icon(Icons.Outlined.Save, null, Modifier.size(17.dp))
                                Spacer(Modifier.width(6.dp))
                                Text("保存为本机新章节")
                            }
                        }
                    }
                }
            }
        }

        Surface(color = SimingPaperWarm, tonalElevation = 4.dp) {
            Column(
                modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 12.dp, vertical = 9.dp),
                verticalArrangement = Arrangement.spacedBy(7.dp),
            ) {
                Row(
                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    assistantScopes.forEach { (value, label) ->
                        AssistChip(
                            onClick = { scope = value },
                            label = { Text(label) },
                            colors = AssistChipDefaults.assistChipColors(
                                containerColor = if (scope == value) MaterialTheme.colorScheme.primaryContainer else Color.White,
                            ),
                        )
                    }
                }

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
                        IconButton(onClick = viewModel::cancelAssistant, modifier = Modifier.size(50.dp)) {
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
                                    scope,
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

internal val assistantScopes = listOf(
    "project" to "全书",
    "outline" to "大纲",
    "characters" to "角色",
    "worldbuilding" to "世界观",
)
