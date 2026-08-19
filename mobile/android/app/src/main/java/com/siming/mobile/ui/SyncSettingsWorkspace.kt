package com.siming.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.LibraryBooks
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.CloudQueue
import androidx.compose.material.icons.outlined.Code
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Devices
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.siming.mobile.BuildConfig
import com.siming.mobile.data.local.GatewayConnection
import com.siming.mobile.data.local.LocalConflict
import com.siming.mobile.data.network.DirectApiSummary

internal fun syncHealthTitle(
    connected: Boolean,
    pending: Int,
    conflicts: Int,
    lastError: String?,
): String = when {
    !connected -> "仅保存在这台手机"
    conflicts > 0 -> "有 $conflicts 个版本需要选择"
    !lastError.isNullOrBlank() -> "上次同步没有完成"
    pending > 0 -> "有 $pending 项等待同步"
    else -> "已经同步"
}

internal fun syncHealthDetail(
    connected: Boolean,
    pending: Int,
    conflicts: Int,
): String = when {
    !connected -> "作品仍可离线编辑；连接自己的 Gateway 后再进行跨设备同步。"
    conflicts > 0 -> "两边的原始版本都保留着，选择采用哪一份不会静默覆盖另一份。"
    pending > 0 -> "手机修改已进入可靠队列，点击同步会先上传本机修改，再拉取 PC 新修订。"
    else -> "手机与 Gateway 当前没有待处理修改。"
}

private fun syncEntityLabel(type: String): String = when (type) {
    "chapter" -> "章节"
    "outline" -> "大纲"
    "character" -> "角色"
    "world" -> "世界观"
    "foreshadowing" -> "伏笔"
    "governance" -> "叙事承诺"
    else -> "资料"
}

@Composable
internal fun MobileSyncWorkspace(
    modifier: Modifier,
    viewModel: MainViewModel,
    connection: GatewayConnection?,
    onScanQr: () -> Unit,
) {
    val pending by viewModel.pendingCount.collectAsStateWithLifecycle()
    val cursor by viewModel.cursor.collectAsStateWithLifecycle()
    val conflicts by viewModel.conflicts.collectAsStateWithLifecycle()
    val ui by viewModel.uiState
    var showConnectionDetails by remember { mutableStateOf(false) }
    var showDisconnect by remember { mutableStateOf(false) }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(18.dp, 18.dp, 18.dp, 104.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            ScreenHeading(
                kicker = "",
                title = "同步",
                detail = "这里只处理跨设备状态；写作和资料编辑始终可以先在手机完成。",
            )
        }
        item {
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = if (conflicts.isNotEmpty() || cursor?.lastError != null) {
                        MaterialTheme.colorScheme.errorContainer
                    } else {
                        SimingPaperWarm
                    },
                ),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            when {
                                connection == null -> Icons.Outlined.CloudOff
                                conflicts.isNotEmpty() -> Icons.Outlined.WarningAmber
                                cursor?.lastError != null -> Icons.Outlined.ErrorOutline
                                pending > 0 -> Icons.Outlined.CloudQueue
                                else -> Icons.Outlined.CheckCircle
                            },
                            null,
                            tint = when {
                                conflicts.isNotEmpty() || cursor?.lastError != null -> MaterialTheme.colorScheme.error
                                connection == null -> MaterialTheme.colorScheme.onSurfaceVariant
                                else -> SimingGreen
                            },
                        )
                        Spacer(Modifier.width(10.dp))
                        Column(Modifier.weight(1f)) {
                            Text(
                                syncHealthTitle(connection != null, pending, conflicts.size, cursor?.lastError),
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Text(
                                syncHealthDetail(connection != null, pending, conflicts.size),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    if (connection == null) {
                        Button(onClick = onScanQr, modifier = Modifier.fillMaxWidth()) {
                            Icon(Icons.Outlined.Devices, null)
                            Spacer(Modifier.width(7.dp))
                            Text("连接 Gateway")
                        }
                    } else {
                        Button(onClick = viewModel::syncNow, enabled = !ui.busy, modifier = Modifier.fillMaxWidth()) {
                            Icon(Icons.Outlined.Sync, null)
                            Spacer(Modifier.width(7.dp))
                            Text(if (ui.busy) ui.activity.ifBlank { "正在同步…" } else "立即同步")
                        }
                    }
                }
            }
        }
        if (connection != null) {
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(9.dp), modifier = Modifier.fillMaxWidth()) {
                    SyncMetric("待上传", pending.toString(), Modifier.weight(1f), pending > 0)
                    SyncMetric("分岔", conflicts.size.toString(), Modifier.weight(1f), conflicts.isNotEmpty())
                    SyncMetric("游标", (cursor?.cursor ?: 0).toString(), Modifier.weight(1f), false)
                }
            }
            item {
                OutlinedCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.Devices, null, tint = SimingGreen)
                            Spacer(Modifier.width(9.dp))
                            Column(Modifier.weight(1f)) {
                                Text(connection.gatewayName, fontWeight = FontWeight.SemiBold)
                                Text("已授权跨设备同步", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            MicroTag("已连接", SimingGreen)
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = viewModel::bootstrap, enabled = !ui.busy, modifier = Modifier.weight(1f)) {
                                Icon(Icons.Outlined.Refresh, null)
                                Spacer(Modifier.width(5.dp))
                                Text("重新校验")
                            }
                            OutlinedButton(onClick = { showConnectionDetails = true }, modifier = Modifier.weight(1f)) {
                                Text("连接详情")
                            }
                        }
                    }
                }
            }
        }
        if (cursor?.lastError != null) {
            item { StatusBanner(Icons.Outlined.ErrorOutline, "上次同步未完成", cursor?.lastError.orEmpty(), warning = true) }
        }
        item {
            Text("版本分岔", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Text("只有同一份资料在手机和 PC 都离线修改时才会出现。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (conflicts.isEmpty()) {
            item { StatusBanner(Icons.Outlined.CheckCircle, "没有需要选择的版本", "当前所有设备沿同一条修订线继续。") }
        } else {
            items(conflicts, key = { it.id }) { conflict ->
                MobileConflictCard(conflict, viewModel)
            }
        }
        if (connection != null) {
            item {
                TextButton(onClick = { showDisconnect = true }, modifier = Modifier.fillMaxWidth()) {
                    Text("断开这台设备", color = MaterialTheme.colorScheme.error)
                }
            }
        }
    }

    if (showConnectionDetails && connection != null) {
        AlertDialog(
            onDismissRequest = { showConnectionDetails = false },
            title = { Text("Gateway 连接详情") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(connection.gatewayName, fontWeight = FontWeight.SemiBold)
                    SelectionContainer { Text(connection.baseUrl, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall) }
                    Text("设备角色：${connection.deviceRole}")
                    Text("同步协议：v${connection.protocolVersion}")
                    SelectionContainer {
                        Text("指纹 ${connection.gatewayFingerprint.chunked(4).joinToString(" ")}", fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.labelSmall)
                    }
                }
            },
            confirmButton = { TextButton(onClick = { showConnectionDetails = false }) { Text("关闭") } },
        )
    }

    if (showDisconnect) {
        AlertDialog(
            onDismissRequest = { showDisconnect = false },
            title = { Text("断开 Gateway？") },
            text = { Text("可以只撤销跨设备授权并保留本机作品，也可以同时清除这台手机的离线副本。") },
            confirmButton = {
                TextButton(onClick = { showDisconnect = false; viewModel.disconnect(false) }) { Text("保留离线作品") }
            },
            dismissButton = {
                TextButton(onClick = { showDisconnect = false; viewModel.disconnect(true) }) {
                    Text("同时清除副本", color = MaterialTheme.colorScheme.error)
                }
            },
        )
    }
}

@Composable
private fun SyncMetric(label: String, value: String, modifier: Modifier, warning: Boolean) {
    Surface(
        color = if (warning) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(12.dp),
        modifier = modifier,
    ) {
        Column(Modifier.padding(13.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun MobileConflictCard(conflict: LocalConflict, viewModel: MainViewModel) {
    var expanded by remember { mutableStateOf(false) }
    OutlinedCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(15.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.WarningAmber, null, tint = MaterialTheme.colorScheme.error)
                Spacer(Modifier.width(8.dp))
                Column(Modifier.weight(1f)) {
                    Text("${syncEntityLabel(conflict.entityType)}在两端都被修改", fontWeight = FontWeight.SemiBold)
                    Text("选择要继续使用的版本；另一份原始快照仍保留。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { viewModel.resolveConflict(conflict, "server") }, modifier = Modifier.weight(1f)) { Text("保留 PC") }
                Button(onClick = { viewModel.resolveConflict(conflict, "client") }, modifier = Modifier.weight(1f)) { Text("采用手机") }
            }
            TextButton(onClick = { expanded = !expanded }) { Text(if (expanded) "收起技术快照" else "查看技术快照") }
            if (expanded) {
                Text("Gateway 当前版本", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                SelectionContainer {
                    Text(conflict.serverPayloadJson ?: "（删除记录）", fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.labelSmall, maxLines = 8, overflow = TextOverflow.Ellipsis)
                }
                HorizontalDivider()
                Text("手机离线版本", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                SelectionContainer {
                    Text(conflict.clientPayloadJson ?: "（删除记录）", fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.labelSmall, maxLines = 8, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

@Composable
internal fun MobileSettingsWorkspace(
    modifier: Modifier,
    connection: GatewayConnection?,
    directApi: DirectApiSummary?,
    viewModel: MainViewModel,
    onConfigureApi: () -> Unit,
    onOpenSync: () -> Unit,
) {
    val uriHandler = LocalUriHandler.current
    val ui by viewModel.uiState
    var showApiDetails by remember { mutableStateOf(false) }
    var showClearApi by remember { mutableStateOf(false) }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(18.dp, 18.dp, 18.dp, 104.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { ScreenHeading("", "设置", "模型、跨设备连接和本机数据边界集中放在这里。") }
        item { SettingsSectionTitle("AI 模型") }
        item {
            OutlinedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.Key, null, tint = if (directApi != null) SimingGreen else MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(9.dp))
                        Column(Modifier.weight(1f)) {
                            Text(directApi?.displayName ?: "手机直连 API", fontWeight = FontWeight.SemiBold)
                            Text(
                                directApi?.model ?: "未配置；仍可连接 Gateway 使用 PC 模型",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (directApi != null) MicroTag("可用", SimingGreen)
                    }
                    if (directApi == null) {
                        Button(onClick = onConfigureApi, modifier = Modifier.fillMaxWidth()) { Text("配置云端 API") }
                    } else {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = viewModel::testDirectApi, enabled = !ui.busy, modifier = Modifier.weight(1f)) { Text("测试") }
                            OutlinedButton(onClick = onConfigureApi, modifier = Modifier.weight(1f)) { Text("编辑") }
                            OutlinedButton(onClick = { showApiDetails = true }, modifier = Modifier.weight(1f)) { Text("详情") }
                        }
                    }
                }
            }
        }
        item { SettingsSectionTitle("跨设备") }
        item {
            OutlinedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(if (connection != null) Icons.Outlined.Devices else Icons.Outlined.CloudOff, null, tint = if (connection != null) SimingGreen else MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(9.dp))
                        Column(Modifier.weight(1f)) {
                            Text(connection?.gatewayName ?: "尚未连接 Gateway", fontWeight = FontWeight.SemiBold)
                            Text(
                                if (connection != null) "跨设备同步已启用" else "不影响手机本地写作和直连 API",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    OutlinedButton(onClick = onOpenSync, modifier = Modifier.fillMaxWidth()) {
                        Icon(Icons.Outlined.Sync, null)
                        Spacer(Modifier.width(7.dp))
                        Text(if (connection != null) "查看同步状态" else "前往连接 Gateway")
                    }
                }
            }
        }
        item { SettingsSectionTitle("数据与隐私") }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = SimingPaperWarm), modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(17.dp), verticalArrangement = Arrangement.spacedBy(13.dp)) {
                    SettingsInfoRow(Icons.AutoMirrored.Outlined.LibraryBooks, "作品优先保存在你的设备", "离线修改进入本地数据库与可靠同步队列。")
                    SettingsInfoRow(Icons.Outlined.Lock, "API Key 由 Android Keystore 保护", "手机私有 Key 不作为普通配置明文保存。")
                    SettingsInfoRow(Icons.Outlined.Devices, "Gateway 是你自己的 PC", "跨设备同步与 PC 工作流不会经过司命官方中转正文。")
                }
            }
        }
        item { SettingsSectionTitle("关于") }
        item {
            OutlinedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.Info, null, tint = SimingCinnabar)
                        Spacer(Modifier.width(9.dp))
                        Column(Modifier.weight(1f)) {
                            Text("司命 ${BuildConfig.VERSION_NAME}", fontWeight = FontWeight.SemiBold)
                            Text("同步协议 v${BuildConfig.SYNC_PROTOCOL_VERSION}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                    OutlinedButton(onClick = { uriHandler.openUri("https://github.com/teangtang1122/siming-ai") }, modifier = Modifier.fillMaxWidth()) {
                        Icon(Icons.Outlined.Code, null)
                        Spacer(Modifier.width(7.dp))
                        Text("开源代码与许可证")
                    }
                }
            }
        }
        if (directApi != null) {
            item {
                TextButton(onClick = { showClearApi = true }, modifier = Modifier.fillMaxWidth()) {
                    Icon(Icons.Outlined.DeleteOutline, null, tint = MaterialTheme.colorScheme.error)
                    Spacer(Modifier.width(7.dp))
                    Text("移除手机 API 配置", color = MaterialTheme.colorScheme.error)
                }
            }
        }
    }

    if (showApiDetails && directApi != null) {
        AlertDialog(
            onDismissRequest = { showApiDetails = false },
            title = { Text("手机直连 API") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(directApi.displayName, fontWeight = FontWeight.SemiBold)
                    Text("模型：${directApi.model}")
                    SelectionContainer { Text(directApi.baseUrl, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall) }
                    Text("这些技术信息默认收起，不影响日常写作。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            },
            confirmButton = { TextButton(onClick = { showApiDetails = false }) { Text("关闭") } },
        )
    }

    if (showClearApi) {
        AlertDialog(
            onDismissRequest = { showClearApi = false },
            title = { Text("移除手机 API 配置？") },
            text = { Text("只会删除 Android Keystore 保护的 API 配置；本机作品和 Gateway 配对不会受影响。") },
            confirmButton = {
                TextButton(onClick = { showClearApi = false; viewModel.clearDirectApi() }) {
                    Text("确认移除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { showClearApi = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun SettingsSectionTitle(title: String) {
    Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
}

@Composable
private fun SettingsInfoRow(icon: androidx.compose.ui.graphics.vector.ImageVector, title: String, detail: String) {
    Row(verticalAlignment = Alignment.Top) {
        Icon(icon, null, tint = SimingCinnabar)
        Spacer(Modifier.width(10.dp))
        Column {
            Text(title, fontWeight = FontWeight.SemiBold)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
