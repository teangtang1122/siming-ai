package com.siming.mobile.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp

/** Full-width rows keep labels usable with the author's system font size. */
@Composable
internal fun WorkspaceActionRow(
    title: String, detail: String, icon: ImageVector, onClick: () -> Unit,
    modifier: Modifier = Modifier, enabled: Boolean = true,
) {
    Row(modifier.fillMaxWidth().clickable(enabled = enabled, onClick = onClick)
        .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Icon(icon, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(22.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, Modifier.size(18.dp),
            tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
internal fun MessageComposer(
    value: String, onValueChange: (String) -> Unit, placeholder: String,
    running: Boolean, canSend: Boolean, onSend: () -> Unit, onStop: (() -> Unit)? = null,
) {
    Surface(color = MaterialTheme.colorScheme.surface) {
        Row(Modifier.fillMaxWidth().testTag("message-composer").padding(horizontal = 12.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Bottom) {
            OutlinedTextField(value = value, onValueChange = onValueChange,
                placeholder = { Text(placeholder, style = MaterialTheme.typography.bodyMedium) },
                textStyle = MaterialTheme.typography.bodyMedium, minLines = 1, maxLines = 4,
                modifier = Modifier.weight(1f), shape = MaterialTheme.shapes.medium)
            if (running && onStop != null) {
                FilledTonalIconButton(onClick = onStop, modifier = Modifier.size(48.dp)) {
                    Icon(Icons.Outlined.Stop, "停止生成", tint = MaterialTheme.colorScheme.error)
                }
            } else {
                FilledIconButton(onClick = onSend, enabled = canSend && !running && value.isNotBlank(),
                    modifier = Modifier.size(48.dp)) { Icon(Icons.AutoMirrored.Outlined.Send, "发送") }
            }
        }
    }
}

internal data class CreationStageItem(val key: String, val label: String, val status: String)

internal fun creationStageStatusLabel(status: String): String = when (status) {
    "confirmed" -> "已确认"
    "generated" -> "待确认"
    "stale", "conflict" -> "待复核"
    else -> "待生成"
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun CreationStageSheet(
    stages: List<CreationStageItem>, selected: String? = null,
    onSelected: (String) -> Unit, onDismiss: () -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Text("立项资料", style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
        Text("${stages.count { it.status == "confirmed" }} / ${stages.size} 项已确认 · 点选查看与编辑",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
        LazyColumn(contentPadding = PaddingValues(bottom = 24.dp)) {
            itemsIndexed(stages, key = { _, it -> it.key }) { index, stage ->
                Surface(color = if (selected == stage.key) MaterialTheme.colorScheme.primaryContainer
                    else MaterialTheme.colorScheme.surface) {
                    Row(Modifier.fillMaxWidth().clickable { onSelected(stage.key) }
                        .padding(horizontal = 20.dp, vertical = 14.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        if (stage.status == "confirmed") Icon(Icons.Outlined.CheckCircle, null,
                            tint = SimingGreen, modifier = Modifier.size(24.dp))
                        else Text("%02d".format(index + 1), style = MaterialTheme.typography.labelLarge,
                            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.width(24.dp))
                        Text(stage.label, style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                        Text(creationStageStatusLabel(stage.status), style = MaterialTheme.typography.labelMedium,
                            color = if (stage.status == "confirmed") SimingGreen else MaterialTheme.colorScheme.primary)
                    }
                }
                HorizontalDivider(Modifier.padding(horizontal = 20.dp))
            }
        }
    }
}
