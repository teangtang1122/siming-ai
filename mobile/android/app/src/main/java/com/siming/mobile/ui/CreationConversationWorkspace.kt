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
    onBack: () -> Unit,
    onOpenDossier: () -> Unit,
    onSend: (String) -> Unit,
    onDiscard: () -> Unit,
    onOpenProject: (String) -> Unit,
) {
    val draft = session.objectValue("draft")
    val messages = (draft["agent_history"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
    var input by rememberSaveable(session.string("id")) { mutableStateOf("") }
    val projectId = session.string("created_project_id")
    val route = draft.string("execution_route")
    val host = draft.string("execution_host")

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
                AgentBubble(message.string("role"), message.string("content"))
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
                            Text(activity.ifBlank { "正在读取资料、执行工具并写入确定事实…" }, color = Color.White.copy(alpha = 0.72f), style = MaterialTheme.typography.bodySmall)
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

@Composable
private fun AgentBubble(role: String, content: String) {
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
            }
        }
    }
}

private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
private fun JsonObject.int(name: String): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: 0
private fun JsonObject.stageState(stage: String): JsonObject = objectValue("draft").objectValue("stages").objectValue(stage)
