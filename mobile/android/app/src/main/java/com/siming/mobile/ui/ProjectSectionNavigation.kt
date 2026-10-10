package com.siming.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.MenuBook
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp

internal data class ProjectPrimaryDestination(
    val key: String, val label: String, val icon: ImageVector, val defaultSection: String,
)

internal val projectReferenceSections = listOf(
    "character" to "角色", "world" to "世界观", "foreshadowing" to "伏笔", "governance" to "叙事治理",
)

internal val projectPrimaryDestinations = listOf(
    ProjectPrimaryDestination("chapter", "写作", Icons.AutoMirrored.Outlined.MenuBook, "chapter"),
    ProjectPrimaryDestination("outline", "大纲", Icons.Outlined.AccountTree, "outline"),
    ProjectPrimaryDestination("assistant", "助手", Icons.Outlined.AutoAwesome, "assistant"),
    ProjectPrimaryDestination("reference", "资料", Icons.Outlined.FolderOpen, "reference"),
)

internal fun projectPrimaryKey(section: String): String = when (section) {
    "chapter", "outline", "assistant" -> section
    else -> "reference"
}

@Composable
internal fun ProjectPrimaryNavigation(selected: String, onSelected: (String) -> Unit) {
    NavigationBar(containerColor = SimingPaperWarm, tonalElevation = 0.dp) {
        projectPrimaryDestinations.forEach { destination ->
            NavigationBarItem(selected = projectPrimaryKey(selected) == destination.key,
                onClick = { onSelected(destination.defaultSection) },
                icon = { Icon(destination.icon, null) }, label = { Text(destination.label) })
        }
    }
}

@Composable
internal fun ProjectReferenceHome(onSelected: (String) -> Unit, onEditProject: () -> Unit) {
    LazyColumn(contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)) {
        item {
            Text("故事资料", style = MaterialTheme.typography.headlineSmall)
            Text("把人物、规则与故事线索放在一起。", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        item {
            OutlinedCard {
                WorkspaceActionRow("作品信息", "书名与简介", Icons.Outlined.Edit, onEditProject)
                HorizontalDivider()
                WorkspaceActionRow("角色", "人物设定与关系", Icons.Outlined.Person, { onSelected("character") })
                HorizontalDivider()
                WorkspaceActionRow("世界观", "地点、势力与世界规则", Icons.Outlined.Public, { onSelected("world") })
                HorizontalDivider()
                WorkspaceActionRow("伏笔", "铺设与回收", Icons.Outlined.Link, { onSelected("foreshadowing") })
                HorizontalDivider()
                WorkspaceActionRow("叙事治理", "故事承诺与连续性", Icons.Outlined.FactCheck, { onSelected("governance") })
            }
        }
        item {
            OutlinedCard {
                WorkspaceActionRow("建档与导出", "整理章节资料、查看任务、导出作品", Icons.Outlined.Inventory2,
                    { onSelected("tools") })
            }
        }
    }
}
