package com.siming.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.FileOpen
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.siming.mobile.data.local.ProjectSyncRecord
import com.siming.mobile.data.local.ProjectSyncStatus
import com.siming.mobile.data.local.syncStatus

@Composable
internal fun LibraryActionPanel(
    onStartAiCreation: () -> Unit,
    onCreateBlank: () -> Unit,
    onImportNovel: () -> Unit,
    onImportProjectPackage: () -> Unit,
) {
    Column {
        WorkspaceActionRow("AI 立项", "从故事想法到开篇大纲", Icons.Outlined.AutoAwesome, onStartAiCreation)
        WorkspaceActionRow("空白作品", "自己构思，直接开写", Icons.Outlined.Add, onCreateBlank)
        WorkspaceActionRow("导入小说", "TXT / Markdown / DOCX", Icons.Outlined.FileOpen, onImportNovel)
        WorkspaceActionRow("导入项目包", "恢复作品、设定与历史", Icons.Outlined.Archive, onImportProjectPackage)
    }
}

@Composable
internal fun MobileProjectCard(
    record: ProjectSyncRecord,
    onClick: () -> Unit,
    onDelete: () -> Unit,
) {
    val project = record.project
    val title = project.formText("title").ifBlank { "未命名作品" }
    val description = project.formText("description")
    var menuExpanded by remember { mutableStateOf(false) }
    Card(
        onClick = onClick,
        colors = CardDefaults.cardColors(containerColor = SimingSurfaceRaised),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(start = 15.dp, top = 15.dp, bottom = 15.dp, end = 7.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Surface(
                color = when {
                    project.conflicted -> MaterialTheme.colorScheme.error.copy(alpha = 0.11f)
                    project.dirty -> MaterialTheme.colorScheme.secondaryContainer
                    else -> MaterialTheme.colorScheme.primaryContainer
                },
                shape = MaterialTheme.shapes.medium,
                modifier = Modifier.size(54.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Text(
                        title.firstOrNull { it.isLetterOrDigit() }?.toString() ?: "书",
                        color = if (project.conflicted) MaterialTheme.colorScheme.error else SimingCinnabar,
                        fontWeight = FontWeight.Bold,
                        fontSize = 21.sp,
                    )
                }
            }
            Spacer(Modifier.width(13.dp))
            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(5.dp),
            ) {
                Text(
                    title,
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (description.isNotBlank()) {
                    Text(
                        description,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    when {
                        project.conflicted -> MicroTag("待处理分岔", MaterialTheme.colorScheme.error)
                        else -> when (record.syncStatus) {
                            ProjectSyncStatus.LOCAL_ONLY -> MicroTag("仅本机", SimingBlue)
                            ProjectSyncStatus.SYNC_PENDING -> MicroTag("待同步", SimingBlue)
                            ProjectSyncStatus.SYNCED -> MicroTag("已同步", SimingGreen)
                            ProjectSyncStatus.UNCONFIRMED -> MicroTag("同步待确认", SimingBlue)
                        }
                    }
                }
            }
            Box {
                IconButton(onClick = { menuExpanded = true }) {
                    Icon(Icons.Outlined.MoreVert, contentDescription = "作品操作")
                }
                DropdownMenu(
                    expanded = menuExpanded,
                    onDismissRequest = { menuExpanded = false },
                ) {
                    DropdownMenuItem(
                        text = { Text("删除作品", color = MaterialTheme.colorScheme.error) },
                        leadingIcon = {
                            Icon(Icons.Outlined.DeleteOutline, contentDescription = null, tint = MaterialTheme.colorScheme.error)
                        },
                        onClick = {
                            menuExpanded = false
                            onDelete()
                        },
                    )
                }
            }
        }
    }
}
