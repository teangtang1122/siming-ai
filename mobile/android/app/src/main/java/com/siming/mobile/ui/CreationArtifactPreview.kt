package com.siming.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

@Composable
internal fun EmptyArtifact(label: String) {
    Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
        Text("$label 还没有内容", fontWeight = FontWeight.SemiBold)
        Text(
            "可以直接生成，也可以回到对话中先补充这一部分的要求。生成结果不会自动确认。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
internal fun ConceptSelector(
    data: JsonObject,
    selectedId: String,
    onSelect: (String) -> Unit,
    enabled: Boolean,
) {
    val options = (data["options"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        options.forEachIndexed { index, concept ->
            val id = concept.string("id").ifBlank { "concept-${index + 1}" }
            OutlinedCard(
                onClick = { onSelect(id) },
                enabled = enabled,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(17.dp),
                border = BorderStroke(
                    if (id == selectedId) 1.8.dp else 1.dp,
                    if (id == selectedId) SimingCinnabar else MaterialTheme.colorScheme.outlineVariant,
                ),
                colors = CardDefaults.outlinedCardColors(
                    containerColor = if (id == selectedId) Color(0xFFFFF4EF) else Color.White,
                ),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(concept.string("title").ifBlank { "创意方向 ${index + 1}" }, fontWeight = FontWeight.Bold, fontSize = 17.sp, modifier = Modifier.weight(1f))
                        if (id == selectedId) {
                            Surface(color = SimingCinnabar.copy(alpha = 0.12f), shape = RoundedCornerShape(10.dp)) {
                                Text("已选择", color = SimingCinnabar, fontSize = 11.sp, modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp))
                            }
                        }
                    }
                    concept.string("subtitle").takeIf(String::isNotBlank)?.let {
                        Text(it, color = SimingCinnabar, style = MaterialTheme.typography.labelMedium)
                    }
                    Text(concept.string("logline").ifBlank { "暂无一句话梗概" }, lineHeight = 21.sp)
                    val protagonist = concept.objectValue("protagonist_seed")
                    if (protagonist.isNotEmpty()) {
                        Text(
                            "主角：${protagonist.string("name").ifBlank { "待定" }} · ${protagonist.string("identity").ifBlank { "身份待定" }}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    concept.string("core_conflict").takeIf(String::isNotBlank)?.let {
                        Text("核心冲突：$it", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
        if (options.isEmpty()) EmptyArtifact("创意方向")
    }
}

@Composable
internal fun ArtifactPreview(data: JsonObject) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        data.entries.forEach { (key, value) ->
            if (key !in setOf("selected_concept_id")) {
                ArtifactField(fieldLabel(key), value, depth = 0)
            }
        }
    }
}

@Composable
private fun ArtifactField(label: String, value: JsonElement, depth: Int) {
    when (value) {
        JsonNull -> Unit
        is JsonPrimitive -> {
            val text = value.contentOrNull.orEmpty()
            if (text.isNotBlank()) {
                Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text(label, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                    SelectionContainer { Text(text, lineHeight = 21.sp) }
                }
            }
        }
        is JsonArray -> {
            Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                Text("$label（${value.size}）", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                if (value.isEmpty()) {
                    Text("暂无内容", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                } else {
                    value.take(24).forEachIndexed { index, item ->
                        when (item) {
                            is JsonObject -> ObjectPreviewCard(item, index, depth + 1)
                            is JsonPrimitive -> Text("• ${item.contentOrNull.orEmpty()}", lineHeight = 21.sp)
                            else -> Text(item.toString(), style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    if (value.size > 24) {
                        Text("其余 ${value.size - 24} 项可在完整编辑器中查看", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
        is JsonObject -> ObjectPreviewCard(value, 0, depth + 1, label)
    }
}

@Composable
private fun ObjectPreviewCard(
    value: JsonObject,
    index: Int,
    depth: Int,
    fallbackLabel: String = "",
) {
    val title = listOf("title", "name", "label", "id")
        .firstNotNullOfOrNull { key -> value.string(key).takeIf(String::isNotBlank) }
        ?: fallbackLabel.ifBlank { "条目 ${index + 1}" }
    OutlinedCard(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            value.entries
                .filterNot { (key, _) -> key in setOf("title", "name", "label", "id") }
                .take(if (depth > 2) 4 else 10)
                .forEach { (key, child) ->
                    when (child) {
                        JsonNull -> Unit
                        is JsonPrimitive -> child.contentOrNull?.takeIf(String::isNotBlank)?.let {
                            Text("${fieldLabel(key)}：$it", style = MaterialTheme.typography.bodySmall, lineHeight = 19.sp)
                        }
                        is JsonArray -> Text("${fieldLabel(key)}：${child.size} 项", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        is JsonObject -> Text("${fieldLabel(key)}：${child.size} 个字段", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
        }
    }
}


private fun fieldLabel(key: String): String = mapOf(
    "options" to "创意方案",
    "title" to "标题",
    "subtitle" to "差异化定位",
    "logline" to "一句话梗概",
    "protagonist_seed" to "主角种子",
    "world_hook" to "世界钩子",
    "core_conflict" to "核心冲突",
    "story_engine" to "持续推进机制",
    "opening_hook" to "开篇钩子",
    "writing_style" to "正文风格",
    "style_rules" to "文风规则",
    "forbidden_patterns" to "禁用表达",
    "worldbuilding" to "世界观设定",
    "characters" to "角色",
    "relationships" to "人物关系",
    "entries" to "地点与势力",
    "relations" to "结构关系",
    "volumes" to "分卷规划",
    "chapters" to "章节细纲",
    "sections" to "场景事件",
    "summary" to "摘要",
    "planned_summary" to "计划摘要",
    "ready" to "是否可建档",
    "warnings" to "提醒",
    "blocking" to "阻断项",
    "counts" to "对象统计",
)[key] ?: key.replace('_', ' ')

private fun JsonObject.objectValue(name: String): JsonObject =
    get(name) as? JsonObject ?: JsonObject(emptyMap())
private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
