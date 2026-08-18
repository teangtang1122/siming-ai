package com.siming.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

private data class ProjectNavGroup(
    val key: String,
    val label: String,
    val sections: List<Pair<String, String>>,
)

private val projectNavGroups = listOf(
    ProjectNavGroup("create", "创作", listOf("assistant" to "AI 共创", "chapter" to "正文")),
    ProjectNavGroup("structure", "结构", listOf("outline" to "大纲", "character" to "角色", "world" to "世界")),
    ProjectNavGroup("manage", "管理", listOf("foreshadowing" to "伏笔", "governance" to "治理", "tools" to "工具")),
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ProjectSectionNavigation(
    selected: String,
    onSelected: (String) -> Unit,
) {
    val activeGroup = projectNavGroups.firstOrNull { group ->
        group.sections.any { it.first == selected }
    } ?: projectNavGroups.first()
    Surface(color = SimingPaperWarm) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 9.dp),
            verticalArrangement = Arrangement.spacedBy(7.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(7.dp),
            ) {
                projectNavGroups.forEach { group ->
                    OutlinedButton(
                        onClick = { onSelected(group.sections.first().first) },
                        modifier = Modifier.weight(1f),
                    ) {
                        Text(group.label)
                    }
                }
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                activeGroup.sections.forEach { (key, label) ->
                    AssistChip(
                        onClick = { onSelected(key) },
                        label = { Text(label) },
                        colors = AssistChipDefaults.assistChipColors(
                            containerColor = if (selected == key) MaterialTheme.colorScheme.primaryContainer else Color.White,
                            labelColor = if (selected == key) SimingCinnabar else MaterialTheme.colorScheme.onSurface,
                        ),
                    )
                }
            }
        }
    }
}
