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
    val runs by viewModel.catalogingRuns(project.projectId).collectAsStateWithLifecycle(initialValue = emptyList())
    val canCatalog = ui.directApi != null
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
                MicroTag("本机模式", MaterialTheme.colorScheme.secondary)
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
                        if (ui.directApi != null) {
                            "使用手机 API 逐章整理摘要、角色、设定、大纲和治理资料。每章通过校验后一起保存；中断时保留正文和计划，可在这里重试。"
                        } else {
                            "正文已保存在手机。配置手机 API 后即可开始建档。"
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
                            enabled = canCatalog && chapters.isNotEmpty() && !ui.catalogingRunning,
                        ) {
                            Icon(Icons.Outlined.AutoAwesome, null)
                            Spacer(Modifier.width(7.dp))
                            Text(if (canCatalog) "为待建档章节建档" else "配置 API 后可建档")
                        }
                    }
                    runs.take(8).forEach { run ->
                        val chapterTitle = chapters.firstOrNull { it.entityId == run.chapterId }?.text("title").orEmpty()
                        Text("$chapterTitle · " + when (run.status) {
                            "completed" -> "建档完成"
                            "running" -> "正在建档"
                            "cancelled" -> "已取消"
                            "interrupted" -> "上次建档已中断"
                            else -> "建档失败"
                        }, style = MaterialTheme.typography.bodySmall)
                        run.error?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
                        if (run.status in setOf("failed", "cancelled", "interrupted")) {
                            TextButton(onClick = { viewModel.startCataloging(project.projectId, listOf(run.chapterId)) }, enabled = canCatalog && !ui.catalogingRunning) { Text("重试本章建档") }
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
                        "TXT、Word 和 PDF 均在手机生成，包含当前已保存正文。导出后选择保存位置。",
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
                                enabled = true,
                            ) { Text("Word") }
                            OutlinedButton(
                                onClick = { viewModel.prepareExport(project.projectId, "pdf", onExportReady) },
                                enabled = true,
                            ) { Text("PDF") }
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
                        Text("司命项目包", style = MaterialTheme.typography.titleMedium)
                    }
                    Text(
                        "专用 .siming-project 与可读稿件分开。完整档位包含章节、独立草稿、快照和素材；结构档位只含写作设置、大纲、角色与世界观。两者都不包含自动任务、对话、RAG 或模型执行配置。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (!ui.exportRunning) {
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(
                                onClick = {
                                    viewModel.prepareProjectPackageExport(project.projectId, "full", onExportReady)
                                },
                            ) { Text("完整项目包") }
                            OutlinedButton(
                                onClick = {
                                    viewModel.prepareProjectPackageExport(project.projectId, "structure", onExportReady)
                                },
                            ) { Text("结构项目包") }
                        }
                    }
                }
            }
        }
    }
}
