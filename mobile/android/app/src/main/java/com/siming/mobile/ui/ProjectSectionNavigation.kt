package com.siming.mobile.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.MenuBook
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp

internal data class ProjectPrimaryDestination(
    val key: String,
    val label: String,
    val icon: ImageVector,
    val defaultSection: String,
)

internal val projectReferenceSections = listOf(
    "outline" to "大纲",
    "character" to "角色",
    "world" to "世界",
    "foreshadowing" to "伏笔",
    "governance" to "治理",
)

internal val projectPrimaryDestinations = listOf(
    ProjectPrimaryDestination("chapter", "正文", Icons.AutoMirrored.Outlined.MenuBook, "chapter"),
    ProjectPrimaryDestination("assistant", "AI", Icons.Outlined.AutoAwesome, "assistant"),
    ProjectPrimaryDestination("reference", "资料", Icons.Outlined.Hub, "outline"),
    ProjectPrimaryDestination("tools", "工具", Icons.Outlined.Settings, "tools"),
)

internal fun projectPrimaryKey(section: String): String = when (section) {
    in projectReferenceSections.map { it.first } -> "reference"
    "assistant" -> "assistant"
    "tools" -> "tools"
    else -> "chapter"
}

@Composable
internal fun ProjectPrimaryNavigation(
    selected: String,
    preferredReferenceSection: String,
    onSelected: (String) -> Unit,
) {
    val active = projectPrimaryKey(selected)
    NavigationBar(containerColor = SimingPaperWarm, tonalElevation = 0.dp) {
        projectPrimaryDestinations.forEach { destination ->
            NavigationBarItem(
                selected = active == destination.key,
                onClick = {
                    onSelected(
                        if (destination.key == "reference") preferredReferenceSection
                        else destination.defaultSection,
                    )
                },
                icon = { Icon(destination.icon, contentDescription = null) },
                label = { Text(destination.label) },
            )
        }
    }
}

@Composable
internal fun ProjectReferenceNavigation(
    selected: String,
    onSelected: (String) -> Unit,
) {
    val selectedIndex = projectReferenceSections.indexOfFirst { it.first == selected }.coerceAtLeast(0)
    ScrollableTabRow(
        selectedTabIndex = selectedIndex,
        containerColor = SimingPaper,
        edgePadding = 12.dp,
        divider = {},
    ) {
        projectReferenceSections.forEachIndexed { index, (key, label) ->
            Tab(
                selected = index == selectedIndex,
                onClick = { onSelected(key) },
                text = { Text(label) },
            )
        }
    }
}
