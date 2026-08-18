package com.siming.mobile.ui

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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Cancel
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.FilePresent
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.siming.mobile.data.MobileExportFile
import com.siming.mobile.data.local.ReplicaEntity

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ProjectToolsPanel(
    project: ReplicaEntity,
    online: Boolean,
    ui: MobileUiState,
    viewModel: MainViewModel,
    onExportReady: (MobileExportFile) -> Unit,
) {
    val chapters by viewModel.entities(project.projectId, "chapter")
        .collectAsStateWithLifecycle(initialValue = emptyList())
    val totalWords = chapters.sumOf { it.text("content").count { char -> !char.isWhitespace() } }
    val catalogingHere = ui.catalogingProjectId == project.projectId
    val progress = if (ui.catalogingTotal > 0) {
        ui.catalogingCompleted.toFloat() / ui.catalogingTotal.toFloat()
    } else 0f

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp, 18.dp, 16.dp, 96.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            ScreenHeading(
                kicker = "",
                title = "作品工具",
                detail = "建档、导出和作品维护集中在这里。",
            )
        }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                MicroTag("${chapters.size} 章", SimingBlue)
                MicroTag("${totalWords} 字", SimingGreen)
                MicroTag(if (online) "PC 权威模式" else "本机模式", MaterialTheme.colorScheme.secondary)
            }
        }
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Icon(Icons.Outlined.AutoAwesome, null)
                        Text("作品建档", style = MaterialTheme.typography.titleMedium)
                    }
                    Text(
                        if (online) {
                            "使用 PC 与桌面端相同的 Cataloging 流程扫描已导入章节，生成章节摘要、角色/设定变化和可写入候选资料。"
                        } else {
                            "完整作品建档依赖 PC 权威 Cataloging，以保证角色、世界观、摘要和治理数据不会出现两套口径。连接 Gateway 后即可启动。"
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (catalogingHere && ui.catalogingRunning) {
                        LinearProgressIndicator(
                            progress = { progress.coerceIn(0f, 1f) },
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Text(
                            "${ui.catalogingCompleted}/${ui.catalogingTotal} 章 · ${ui.catalogingActivity.ifBlank { "正在建档" }}",
                            style = MaterialTheme.typography.bodySmall,
                        )
                        if (ui.catalogingFailed > 0) {
                            Text("${ui.catalogingFailed} 章需要处理", color = MaterialTheme.colorScheme.error)
                        }
                        TextButton(onClick = { viewModel.cancelCataloging(project.projectId) }) {
                            Icon(Icons.Outlined.Cancel, null)
                            Spacer(Modifier.width(6.dp))
                            Text("取消建档")
                        }
                    } else {
                        Button(
                            onClick = { viewModel.startCataloging(project.projectId) },
                            enabled = online && chapters.isNotEmpty() && !ui.catalogingRunning,
                        ) {
                            Icon(Icons.Outlined.AutoAwesome, null)
                            Spacer(Modifier.width(7.dp))
                            Text(if (online) "开始全书建档" else "连接 PC 后建档")
                        }
                    }
                }
            }
        }
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Icon(Icons.Outlined.Download, null)
                        Text("导出小说", style = MaterialTheme.typography.titleMedium)
                    }
                    Text(
                        if (online) {
                            "TXT、Word 和 PDF 复用 PC 的正式导出服务；导出完成后由 Android 系统文件选择器决定保存位置。"
                        } else {
                            "离线和手机独立模式仍可从本机章节副本导出 TXT；Word / PDF 需要连接 PC。"
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (ui.exportRunning) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            CircularProgressIndicator()
                            Text("正在准备导出文件…")
                        }
                    } else {
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { viewModel.prepareExport(project.projectId, "txt", onExportReady) }) {
                                Icon(Icons.Outlined.FilePresent, null)
                                Spacer(Modifier.width(6.dp))
                                Text("TXT")
                            }
                            OutlinedButton(
                                onClick = { viewModel.prepareExport(project.projectId, "docx", onExportReady) },
                                enabled = online,
                            ) { Text("Word") }
                            OutlinedButton(
                                onClick = { viewModel.prepareExport(project.projectId, "pdf", onExportReady) },
                                enabled = online,
                            ) { Text("PDF") }
                        }
                    }
                }
            }
        }
    }
}
